---
name: cluster-trends
description: Discover emergent topic clusters (what videos are about) and format clusters (how they hold attention) from the collected corpus without a predefined taxonomy, track lineage across runs (emerged/persisted/merged/split/dissolved), and rank evidence-backed opportunities with uncertainty and an exploration share.
---

# Cluster trends and rank opportunities

1. `.venv/bin/dgl cluster --out outputs/clusters/<date>.json`
   - Needs ≥ 6 videos in the 30-day lookback; if `skipped`, report that and stop.
2. Read the output. For each cluster, labels are generated *after* clustering from
   distinguishing terms (topic) or feature deviations (format). You may rewrite a label in plain
   Korean/English, but only from the listed top terms / traits and the member titles — add the
   original label alongside, never replace the member list.
3. Interpret momentum honestly: `median_velocity_vph` exists only for videos with ≥2 snapshots;
   `publication_momentum` counts recent vs prior uploads; say which signal you used.
4. `.venv/bin/dgl rank --clusters outputs/clusters/<date>.json --out outputs/opportunities/<date>.json`
   - Report score with its 10–90% interval, all components, and whether it is an exploration pick.
   - `shareability` is a proxy (comments/likes), not measured shares — say so.
5. Never favour DINARY-related or any pre-chosen category; the ranking decides.
