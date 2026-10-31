---
name: analyze-video
description: Inspect an authorized local video file — frames, cuts, shot rhythm, on-screen text (OCR), transcript, loudness, loopability — and write evidence-linked observations, separating measured facts from interpretation. Use when asked to analyse/watch a video file we are allowed to inspect.
---

# Analyze video

Only analyse media we may inspect: our own productions, licensed footage, creator-permitted
files, or media a platform API legitimately provides. Never download platform media by bypassing
authentication or terms; for everything else record `metadata_only`.

1. Run:
   `.venv/bin/dgl analyze <file> --rights "<why we may analyse this>" --platform <p> --video-id <id> [--srt <captions.srt>] --out outputs/analyses/<id>.json`
2. Check `access_level`: `full_av` (frames + audio), `visual` (no usable audio), `metadata_only`.
   Say which one applied. If the transcript source is `none`, say no speech was transcribed.
3. Look at the evidence frames listed in the output (Read the PNGs) — at least the opening
   0–3 s frames and the last frame — before writing any interpretation.
4. Write the interpretation (hook mechanism, narrative progression, emotional mechanism,
   replay/share incentive) into the JSON's `interpretation` field as:
   `{"claims": [{"claim": "...", "evidence": ["frame path or t=..s"], "confidence": "low|medium|high"}]}`.
   Each claim must cite a frame or timestamp; anything you cannot point to is a hypothesis.
5. Known limitation: hard-cut detection at 0.3 misses text-only changes; rhythm uses the
   calibrated 0.08 edit threshold (`EDIT_THRESHOLD` in `src/dgl/analysis/media.py`).
