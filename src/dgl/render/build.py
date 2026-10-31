"""Production builder: render spec → narration, original music bed, Remotion render, loudness pass, SRT.

A *render spec* is the production-side translation of a concept storyboard:

    {"concept_id": "...", "fps": 30, "duration": 24.0,
     "voice": {"engine": "espeak-ng", "voice": "ko", "rate": 175},
     "music": {"style": "pulse", "bpm": 112, "key_hz": 220.0, "gain_db": -24},
     "scenes": [{"start": 0, "end": 2.2, "layout": "clock", "motion": "punchIn",
                 "bg": "#101418", "main": "07:00", "kicker": "...", "sub": "...",
                 "voice_ko": "spoken line or empty", "sfx": "tick|whoosh|hit|none"}]}

All audio is generated here (TTS + synthesized tones), so the output carries
no third-party audiovisual material. espeak-ng is a functional placeholder
voice; swap `voice.engine` for a licensed Korean TTS before publishing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..analysis.media import _run

RENDER_DIR = Path(__file__).resolve().parents[3] / "render"


def wav_duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.stdout.strip())


def tts(text: str, out: Path, voice: dict[str, Any]) -> Path:
    engine = voice.get("engine", "espeak-ng")
    if engine != "espeak-ng":
        raise NotImplementedError(f"TTS engine {engine} not wired; see docs/INTEGRATIONS.md")
    # espeak reads Arabic numerals in English; spell times/numbers out in the script instead
    _run(["espeak-ng", "-v", voice.get("voice", "ko"), "-s", str(voice.get("rate", 175)), "-p", str(voice.get("pitch", 50)),
          "-w", str(out), text])
    return out


def music_bed(out: Path, duration: float, bpm: int = 112, key_hz: float = 220.0) -> Path:
    """Original synthesized bed: soft two-note pad + a filtered pulse on each beat."""
    beat = 60 / bpm
    pad = (f"sine=f={key_hz}:d={duration},volume=0.5[a];sine=f={key_hz * 1.5}:d={duration},volume=0.35[b];"
           f"sine=f={key_hz * 2}:d={duration},volume=0.15[c];[a][b][c]amix=inputs=3:normalize=0,"
           f"tremolo=f={1 / (beat * 2):.4f}:d=0.4[pad];")
    pulse = (f"sine=f=55:d={duration},volume='if(lt(mod(t,{beat:.4f}),0.09),1.6,0)':eval=frame[kick];"
             f"[pad][kick]amix=inputs=2:normalize=0,lowpass=f=3000,afade=t=in:d=0.3,"
             f"afade=t=out:st={max(duration - 0.6, 0):.2f}:d=0.6[out]")
    _run(["ffmpeg", "-y", "-v", "error", "-filter_complex", pad + pulse, "-map", "[out]", "-ar", "48000",
          "-ac", "2", str(out)])
    return out


def sfx(kind: str, out: Path) -> Path | None:
    graphs = {
        "tick": "sine=f=1800:d=0.05,afade=t=out:d=0.05",
        "whoosh": "anoisesrc=d=0.35:c=pink,bandpass=f=1200:w=900,afade=t=in:d=0.15,afade=t=out:st=0.15:d=0.2",
        "hit": "sine=f=70:d=0.35,afade=t=out:d=0.35,volume=2",
        "ding": "sine=f=1320:d=0.6,afade=t=out:d=0.6",
    }
    if kind not in graphs:
        return None
    _run(["ffmpeg", "-y", "-v", "error", "-filter_complex", graphs[kind], "-ar", "48000", "-ac", "2", str(out)])
    return out


def chunk_caption(text: str, max_chars: int = 16) -> list[str]:
    words, chunks, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(captions: list[dict], out: Path) -> Path:
    out.write_text("\n".join(f"{i}\n{srt_ts(c['start'])} --> {srt_ts(c['end'])}\n{c['text']}\n"
                             for i, c in enumerate(captions, 1)), encoding="utf-8")
    return out


def loudnorm(src: Path, dst: Path, target: float = -14.0, tp: float = -1.5) -> dict[str, Any]:
    """Two-pass EBU R128 normalisation of the audio track; video stream is copied."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    m = json.loads(p.stderr[p.stderr.rfind("{"):p.stderr.rfind("}") + 1])
    af = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "copy", "-af", af, "-ar", "48000", "-c:a", "aac",
          "-b:a", "192k", "-movflags", "+faststart", str(dst)])
    return m


def build(spec: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    job = re.sub(r"[^a-zA-Z0-9_-]", "_", spec["concept_id"])
    pub = RENDER_DIR / "public" / job
    if pub.exists():
        shutil.rmtree(pub)
    pub.mkdir(parents=True)
    fps, duration = spec.get("fps", 30), float(spec["duration"])
    voice = spec.get("voice", {"engine": "espeak-ng", "voice": "ko", "rate": 175})
    audio, captions, warnings = [], [], []

    m = spec.get("music")
    if m:
        music_bed(pub / "music.wav", duration, m.get("bpm", 112), m.get("key_hz", 220.0))
        audio.append({"src": f"{job}/music.wav", "start": 0, "volume": 10 ** (m.get("gain_db", -22) / 20)})

    for i, s in enumerate(spec["scenes"]):
        if s.get("sfx") and (f := sfx(s["sfx"], pub / f"sfx_{i}.wav")):
            audio.append({"src": f"{job}/{f.name}", "start": s["start"], "volume": 0.5})
        line = (s.get("voice_ko") or "").strip()
        if not line:
            continue
        w = tts(line, pub / f"vo_{i}.wav", voice)
        d = wav_duration(w)
        start = s["start"] + s.get("voice_offset", 0.08)
        if start + d > s["end"] + 0.25:
            warnings.append(f"scene {i}: narration {d:.2f}s overruns scene ({s['end'] - s['start']:.2f}s)")
        audio.append({"src": f"{job}/{w.name}", "start": start, "volume": 1.0})
        cap_text = s.get("caption_ko") or line
        chunks = chunk_caption(cap_text)
        total = sum(len(c) for c in chunks)
        t = start
        for c in chunks:
            seg = d * len(c) / total
            captions.append({"start": round(t, 3), "end": round(min(t + seg, duration), 3), "text": c})
            t += seg

    props = {"fps": fps, "duration": duration, "captions": captions, "audio": audio,
             "scenes": [{k: v for k, v in s.items() if k not in ("voice_ko", "caption_ko", "sfx", "voice_offset")}
                        for s in spec["scenes"]]}
    props_path = out_dir / f"{job}.props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")
    raw = out_dir / f"{job}.raw.mp4"
    env = {**os.environ}
    cmd = ["npx", "remotion", "render", "src/index.ts", "Timeline", str(raw.resolve()), f"--props={props_path.resolve()}",
           "--log=error", "--concurrency=4"]
    p = subprocess.run(cmd, cwd=RENDER_DIR, capture_output=True, text=True, timeout=1800, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"remotion render failed: {p.stderr[-1500:]}")
    final = out_dir / f"{job}.mp4"
    ln = loudnorm(raw, final)
    raw.unlink(missing_ok=True)
    srt = write_srt(captions, out_dir / f"{job}.ko.srt")
    return {"video": str(final), "srt": str(srt), "props": str(props_path), "warnings": warnings,
            "loudnorm_input": {k: ln[k] for k in ("input_i", "input_tp")}, "n_captions": len(captions)}
