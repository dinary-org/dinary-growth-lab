---
name: produce-video
description: Build a 9:16 MP4 from a concept — render spec, TTS narration, synthesized music, Remotion render, loudness normalisation, SRT — then run QC (aspect, duration, loudness, true peak, black/frozen frames, subtitles, decode integrity) and queue it for human approval. Never publishes.
---

# Produce video

1. Write `outputs/render_specs/<concept_id>.json` (format documented in `src/dgl/render/build.py`).
   Choose scene layouts per concept (`bigText`, `clock`, `card`, `split`, `reveal`) — do not reuse one
   template for every concept. Keep text inside the safe area (renderer reserves top 220 px / bottom 380 px).
2. Render + QC: `REMOTION_BROWSER_EXECUTABLE=... .venv/bin/dgl render outputs/render_specs/<id>.json`
3. If QC fails, read `outputs/videos/<id>.qc.json`, fix the spec (not the thresholds), re-render.
   Narration-overrun warnings mean the line is longer than its scene: shorten the line or the voice rate.
4. Look at frames (`ffmpeg -ss <t> -i video -frames:v 1 x.png`, then Read) before reporting.
5. Approval is a human action: `.venv/bin/dgl approve <production_id> --by <name>` (uploads to R2
   when configured). Publishing stays manual; afterwards register it with `dgl post-register`.
6. espeak-ng narration is a placeholder — flag it in the report and recommend a licensed voice.
