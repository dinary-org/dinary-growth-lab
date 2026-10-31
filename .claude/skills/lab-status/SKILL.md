---
name: lab-status
description: Check which DINARY Growth Lab integrations actually work in this session (YouTube API, Apify, Supabase, R2, Runway, Claude API, FFmpeg, OCR, TTS, Remotion) and report WORKING / BLOCKED / NOT TESTED with the exact missing credential or blocked host. Use at the start of any growth-lab session or when a pipeline step fails.
---

# Lab status

1. Make sure the environment is set up: `bash scripts/setup.sh` (idempotent).
2. Run `.venv/bin/dgl status` — every check performs a real call; a reachable host
   without a key is still BLOCKED.
3. Report the table verbatim. For each BLOCKED row say exactly what is missing:
   - missing env var → name it (see `.env.example`); in Claude Code Cloud it is added in the
     environment settings, never pasted into chat;
   - `ProxyError` / `unreachable` → the host must be allowed in the environment's network policy.
4. Never treat an installed package as a working integration.
