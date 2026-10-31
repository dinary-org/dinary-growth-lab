"""Pre-publication quality checks for rendered vertical videos.

Every check returns pass/fail plus the measured value, so a failed render can
be diagnosed without re-running. A production is only eligible for human
approval when all `blocking` checks pass.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ..analysis import media


def _check(name: str, ok: bool, value: Any, expected: str, blocking: bool = True) -> dict[str, Any]:
    return {"check": name, "pass": bool(ok), "value": value, "expected": expected, "blocking": blocking}


def decode_errors(path: Path) -> list[str]:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True, timeout=900)
    return [l for l in p.stderr.splitlines() if l.strip()]


def freezes(path: Path, min_d: float = 2.5) -> list[float]:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", f"freezedetect=n=0.001:d={min_d}",
                        "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=900)
    return [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", p.stderr)]


def run_qc(
    path: Path,
    srt: Path | None,
    expected_duration: float | None = None,
    work_dir: Path | None = None,
    target_lufs: float = -14.0,
    lufs_tolerance: float = 2.0,
) -> dict[str, Any]:
    path = Path(path)
    results: list[dict[str, Any]] = []
    if not path.exists() or path.stat().st_size == 0:
        return {"pass": False, "checks": [_check("file_exists", False, None, "non-empty file")]}
    errs = decode_errors(path)
    results.append(_check("render_integrity", not errs, errs[:5], "no decoder errors"))
    meta = media.probe(path)
    results.append(_check("resolution", (meta["width"], meta["height"]) == (1080, 1920),
                          f"{meta['width']}x{meta['height']}", "1080x1920"))
    results.append(_check("aspect_9_16", abs(meta["width"] / meta["height"] - 9 / 16) < 0.01,
                          round(meta["width"] / meta["height"], 4), "0.5625"))
    results.append(_check("fps", meta["fps"] is not None and 23.9 <= meta["fps"] <= 60.1, meta["fps"], "24–60"))
    results.append(_check("codec", meta["video_codec"] == "h264", meta["video_codec"], "h264 (widest platform support)"))
    d = meta["duration_s"]
    if expected_duration:
        results.append(_check("duration", abs(d - expected_duration) <= 1.0, round(d, 2), f"{expected_duration}±1s"))
    results.append(_check("duration_platform_limits", 3 <= d <= 90, round(d, 2),
                          "3–90 s (fits Reels and Shorts)"))
    results.append(_check("audio_present", meta["has_audio"], meta["audio_codec"], "audio track (aac)"))
    if meta["has_audio"]:
        loud = media.loudness(path)
        i, tp = loud["integrated_lufs"], loud["true_peak_dbfs"]
        results.append(_check("loudness_integrated", i is not None and abs(i - target_lufs) <= lufs_tolerance,
                              i, f"{target_lufs}±{lufs_tolerance} LUFS"))
        results.append(_check("true_peak", tp is not None and tp <= -1.0, tp, "≤ -1.0 dBFS"))
    blacks = media.black_segments(path, min_d=0.3)
    results.append(_check("black_frames", not blacks, blacks, "no black segment ≥ 0.3 s"))
    fr = freezes(path)
    results.append(_check("frozen_video", not fr, fr, "no static stretch ≥ 2.5 s", blocking=False))

    # subtitles: sidecar exists and burned-in text is legible at cue midpoints
    if srt and Path(srt).exists():
        cues = media.read_srt(Path(srt))
        covered = sum(c["end"] - c["start"] for c in cues)
        results.append(_check("subtitle_sidecar", bool(cues), len(cues), "SRT with ≥1 cue"))
        results.append(_check("subtitle_coverage", covered / d >= 0.5 if d else False, round(covered / d, 2),
                              "cues cover ≥ 50% of runtime", blocking=False))
        wd = Path(work_dir or path.parent / "qc_frames")
        hits = 0
        sample = cues[:: max(1, len(cues) // 6)][:6]
        for c in sample:
            f = media.extract_frame(path, (c["start"] + c["end"]) / 2, wd / f"cue_{c['start']:.2f}.png", width=1080)
            if re.search(r"[가-힣]{2,}", media.ocr(f, "kor")):
                hits += 1
        results.append(_check("subtitle_burned_in_legible", sample and hits / len(sample) >= 0.6,
                              f"{hits}/{len(sample)} sampled cues OCR-readable", "≥ 60% Korean text detected"))
    else:
        results.append(_check("subtitle_sidecar", False, None, "SRT sidecar present"))
    ok = all(r["pass"] for r in results if r["blocking"])
    return {"pass": ok, "media": meta, "checks": results}
