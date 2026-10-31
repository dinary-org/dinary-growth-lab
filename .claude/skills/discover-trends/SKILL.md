---
name: discover-trends
description: Collect real, recent YouTube Shorts and Instagram Reels candidates with verifiable metrics, re-snapshot tracked videos to measure velocity, and record coverage and sampling bias. Use for recurring discovery runs or when asked what is trending. Never invents videos or numbers.
---

# Discover trends

Principles: no predefined niches; real data only; record what is missing.

1. `.venv/bin/dgl status` — stop and report if `youtube_data_api_v3` (or `apify_instagram`) is BLOCKED.
   Do NOT substitute web-search snippets for platform metrics.
2. Discovery (quota-capped):
   `.venv/bin/dgl discover --platform youtube --queries 6 --chart-pages 4 --days 14`
   - chart sampling (`mostPopular`, region KR) + adaptive search terms learned from the corpus
     + ≥35% exploration/random probes (see `src/dgl/discovery/explore.py`).
   - Instagram: `--platform instagram` (Apify, capped by `APIFY_MAX_USD_PER_RUN`).
3. Velocity needs repeated observations: `.venv/bin/dgl snapshot` (schedule every 6–12 h).
4. Report from the job result: candidates found, short-form kept, distinct creators, quota used,
   errors, and the `sampling_notes`. State explicitly which metrics are missing (shares/saves are
   never public on YouTube; Instagram may hide likes).
5. Persist: the job drains to Supabase automatically when credentials exist; otherwise say the
   data lives only in `data/lab.sqlite` in this (ephemeral) session.
