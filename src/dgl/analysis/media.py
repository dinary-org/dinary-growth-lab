"""Audiovisual inspection of a *local, authorized* media file with FFmpeg + Tesseract.

This module never downloads anything. Callers must supply a file they are
allowed to analyse and state the rights basis (own production, licence,
creator permission, platform API that grants media access). The resulting
record states its access level:

* ``metadata_only`` — no media inspected (built elsewhere, from API metadata)
* ``visual``        — frames inspected, no usable audio track
* ``full_av``       — frames and audio inspected

Every observation carries a timestamp and, for frames, the path of the
extracted evidence image so a human can verify it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-800:]}")
    return p


def probe(path: Path) -> dict[str, Any]:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        raise MediaError("no video stream")
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    return {
        # the video stream bounds what can be seen; container duration may include audio padding
        "duration_s": float(v.get("duration") or data["format"].get("duration", 0)),
        "container_duration_s": float(data["format"].get("duration", 0)),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": round(float(num) / float(den), 3) if float(den) else None,
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "bitrate": int(data["format"].get("bit_rate", 0) or 0),
    }


def dedupe_cuts(cuts: list[float], min_gap: float = 0.25) -> list[float]:
    out: list[float] = []
    for c in cuts:
        if not out or c - out[-1] >= min_gap:
            out.append(c)
    return out


# 0.08 was calibrated on our own renders, whose storyboards give exact ground-truth
# boundaries: it recovered 9/9 edits with no false positives (0.3 found 5/9 because
# text-only changes on a similar background score low; 0.05 added animation noise).
EDIT_THRESHOLD = 0.08
HARD_CUT_THRESHOLD = 0.3


def scene_cuts(path: Path, threshold: float = HARD_CUT_THRESHOLD) -> list[float]:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", f"select='gt(scene,{threshold})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    return [round(float(t), 3) for t in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def black_segments(path: Path, min_d: float = 0.1) -> list[dict[str, float]]:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", f"blackdetect=d={min_d}:pix_th=0.10",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    return [
        {"start": float(a), "end": float(b), "duration": float(c)}
        for a, b, c in re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", p.stderr)
    ]


def loudness(path: Path) -> dict[str, float | None]:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-vn", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    summary = p.stderr[p.stderr.rfind("Summary:"):]
    def grab(pat: str) -> float | None:
        m = re.search(pat, summary)
        return float(m.group(1)) if m else None
    return {
        "integrated_lufs": grab(r"I:\s+(-?[\d.]+) LUFS"),
        "loudness_range_lu": grab(r"LRA:\s+(-?[\d.]+) LU"),
        "true_peak_dbfs": grab(r"Peak:\s+(-?[\d.]+) dBFS"),
    }


def silences(path: Path, noise_db: int = -35, min_d: float = 0.35) -> list[dict[str, float]]:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"silencedetect=n={noise_db}dB:d={min_d}",
         "-vn", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    return [{"start": max(s, 0.0), "end": e} for s, e in zip(starts, ends + [None] * (len(starts) - len(ends))) if e]


def extract_frame(path: Path, t: float, out: Path, width: int = 540) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
          "-vf", f"scale={width}:-2", str(out)])
    if not out.exists():
        raise MediaError(f"no frame decoded at t={t:.3f}s")
    return out


def ocr(image: Path, langs: str = "kor+eng") -> str:
    if not shutil.which("tesseract"):
        return ""
    p = subprocess.run(["tesseract", str(image), "-", "-l", langs, "--psm", "6"],
                       capture_output=True, text=True, timeout=120)
    text = re.sub(r"\s+", " ", p.stdout).strip()
    # drop OCR noise: keep tokens with at least one Hangul/latin letter or digit run
    toks = [t for t in text.split() if re.search(r"[가-힣]{1,}|[A-Za-z]{2,}|\d", t)]
    return " ".join(toks)


def frame_stats(img_path: Path) -> dict[str, float]:
    im = Image.open(img_path).convert("RGB")
    st = ImageStat.Stat(im)
    r, g, b = st.mean
    hsv = im.convert("HSV")
    sat = ImageStat.Stat(hsv).mean[1]
    return {"brightness": round((r * 299 + g * 587 + b * 114) / 1000 / 255, 3), "saturation": round(sat / 255, 3)}


def frame_similarity(a: Path, b: Path) -> float:
    ia = Image.open(a).convert("L").resize((64, 114))
    ib = Image.open(b).convert("L").resize((64, 114))
    diff = ImageStat.Stat(ImageChops.difference(ia, ib)).mean[0] / 255
    return round(1 - diff, 3)


def read_srt(path: Path) -> list[dict[str, Any]]:
    def ts(x: str) -> float:
        h, m, rest = x.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = [x.strip() for x in lines[1].split("-->")]
            cues.append({"start": ts(a), "end": ts(b), "text": " ".join(lines[2:])})
    return cues


@dataclass
class TranscriptResult:
    source: str  # sidecar_srt | whisper_local | none
    cues: list[dict[str, Any]]
    note: str = ""


def transcribe(path: Path, sidecar: Path | None = None, language: str = "ko") -> TranscriptResult:
    if sidecar and sidecar.exists():
        return TranscriptResult("sidecar_srt", read_srt(sidecar), "creator-supplied captions")
    try:
        from faster_whisper import WhisperModel  # optional; model download needs huggingface.co

        model = WhisperModel("small", compute_type="int8")
        segs, _ = model.transcribe(str(path), language=language)
        return TranscriptResult("whisper_local", [{"start": s.start, "end": s.end, "text": s.text} for s in segs])
    except Exception as e:  # noqa: BLE001 - report precisely why there is no transcript
        return TranscriptResult("none", [], f"no transcript: {type(e).__name__}: {e}"[:300])


def analyze_file(
    path: Path,
    evidence_dir: Path,
    rights_basis: str,
    sidecar_srt: Path | None = None,
    opening_points: tuple[float, ...] = (0.0, 0.5, 1.0, 2.0, 3.0),
) -> dict[str, Any]:
    if not rights_basis:
        raise ValueError("rights_basis is required: state why this media may be analysed")
    path = Path(path)
    meta = probe(path)
    dur = meta["duration_s"]
    cuts = dedupe_cuts([c for c in scene_cuts(path, EDIT_THRESHOLD) if 0.05 < c < dur - 0.05])
    hard_cuts = [c for c in scene_cuts(path, HARD_CUT_THRESHOLD) if 0.05 < c < dur - 0.05]
    bounds = [0.0] + cuts + [dur]
    shots = [round(b - a, 3) for a, b in zip(bounds, bounds[1:]) if b - a > 0.02]

    # evidence frames: opening, each shot midpoint (capped), final frame
    points = sorted({round(t, 2) for t in opening_points if t < dur}
                    | {round((a + b) / 2, 2) for a, b in list(zip(bounds, bounds[1:]))[:24]}
                    | {round(max(dur - 0.1, 0), 2)})
    frames = []
    for t in points:
        f = extract_frame(path, t, evidence_dir / f"frame_{t:07.2f}.png")
        frames.append({"t": t, "path": str(f), "ocr": ocr(f), **frame_stats(f)})

    loud = loudness(path) if meta["has_audio"] else {}
    sil = silences(path) if meta["has_audio"] else []
    tr = transcribe(path, sidecar_srt)
    speech_s = sum(c["end"] - c["start"] for c in tr.cues)
    first_text = next((f for f in frames if f["ocr"]), None)
    first_cue = tr.cues[0] if tr.cues else None
    loop_sim = frame_similarity(Path(frames[0]["path"]), Path(frames[-1]["path"])) if len(frames) > 1 else None

    audio_ok = meta["has_audio"] and loud.get("integrated_lufs") not in (None,) and loud["integrated_lufs"] > -70
    access = "full_av" if audio_ok else "visual"
    features = {
        "duration_s": round(dur, 2),
        "aspect": round(meta["width"] / meta["height"], 4),
        "n_shots": len(shots),
        "cuts_per_10s": round(len(cuts) / dur * 10, 2) if dur else 0,
        "mean_shot_s": round(sum(shots) / len(shots), 2) if shots else dur,
        "first_cut_s": cuts[0] if cuts else None,
        "hard_cuts_per_10s": round(len(hard_cuts) / dur * 10, 2) if dur else 0,
        "text_on_screen_ratio": round(sum(1 for f in frames if f["ocr"]) / len(frames), 2) if frames else 0,
        "first_text_s": first_text["t"] if first_text else None,
        "first_speech_s": round(first_cue["start"], 2) if first_cue else None,
        "speech_ratio": round(speech_s / dur, 2) if dur else 0,
        "words_per_s": round(sum(len(c["text"].split()) for c in tr.cues) / dur, 2) if dur and tr.cues else 0,
        "integrated_lufs": loud.get("integrated_lufs"),
        "silence_ratio": round(sum(s["end"] - s["start"] for s in sil) / dur, 2) if dur and sil else 0,
        "mean_brightness": round(sum(f["brightness"] for f in frames) / len(frames), 3),
        "mean_saturation": round(sum(f["saturation"] for f in frames) / len(frames), 3),
        "loop_similarity": loop_sim,
        "ends_with_question": bool(tr.cues and tr.cues[-1]["text"].strip().endswith("?")),
    }
    observations = [
        {"kind": "hook", "t": 0.0,
         "fact": f"first on-screen text at {features['first_text_s']}s; first speech at {features['first_speech_s']}s; "
                 f"first cut at {features['first_cut_s']}s",
         "evidence": [f["path"] for f in frames if f["t"] <= 3.0]},
        {"kind": "rhythm", "fact": f"{len(shots)} shots, mean {features['mean_shot_s']}s, {features['cuts_per_10s']} cuts/10s",
         "evidence": {"cuts": cuts, "shot_lengths": shots}},
        {"kind": "audio", "fact": f"integrated {loud.get('integrated_lufs')} LUFS, true peak {loud.get('true_peak_dbfs')} dBFS, "
                                  f"speech ratio {features['speech_ratio']}", "evidence": {"silences": sil}},
        {"kind": "ending", "t": frames[-1]["t"],
         "fact": f"last-frame/first-frame similarity {loop_sim} (higher → loopable); ends with question: "
                 f"{features['ends_with_question']}", "evidence": [frames[0]["path"], frames[-1]["path"]]},
    ]
    return {
        "access_level": access,
        "rights_basis": rights_basis,
        "media": meta,
        "transcript": {"source": tr.source, "note": tr.note, "cues": tr.cues},
        "frames": frames,
        "cuts": cuts,
        "hard_cuts": hard_cuts,
        "black_segments": black_segments(path),
        "loudness": loud,
        "features": features,
        "observations": observations,
        "interpretation": None,  # filled by the analyze-video skill (Claude), kept separate from measured facts
    }


def metadata_only_record(video: dict) -> dict[str, Any]:
    """Record for videos whose media we cannot (or may not) inspect."""
    title = video.get("title") or ""
    return {
        "access_level": "metadata_only",
        "rights_basis": "public metadata via platform API",
        "features": {
            "duration_s": video.get("duration_s"),
            "title_len": len(title),
            "title_has_question": "?" in title,
            "title_has_number": bool(re.search(r"\d", title)),
            "n_hashtags": len(video.get("hashtags") or []),
        },
        "observations": [{"kind": "note", "fact": "media not inspected; no claims about visuals or audio"}],
    }
