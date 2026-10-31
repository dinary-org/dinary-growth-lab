# Architecture

```
            ┌───────────── discovery (Phase 1) ─────────────┐
 YouTube    │ chart sampling ─┐                              │
 Data API ─▶│ adaptive search ├─▶ hydrate ─▶ videos          │
 Apify    ─▶│ random probes  ─┘     │        video_snapshots │ (repeated → velocity)
            │                       └─▶ creator baselines    │ (→ creator-relative outperformance)
            └───────────────────────────────────────────────┘
                              │
            ┌──── analysis (Phase 2) ────┐
 authorized │ ffprobe, scene cuts, frames │  access_level ∈ {metadata_only, visual, full_av}
 media    ─▶│ OCR, transcript, loudness   │  every observation → timestamp + evidence frame
            │ + Claude interpretation     │  (interpretation kept separate from measurements)
            └─────────────────────────────┘
                              │
            ┌──── emergent clustering (Phase 3) ────────────────────────────┐
            │ topic space  (what):  char n-gram TF-IDF → SVD → HDBSCAN       │
            │ format space (how):   measured features   → HDBSCAN / k by sil │
            │ labels AFTER clustering · lineage across runs ·                │
            │ momentum / decline vs predecessor · members kept for tracing   │
            └────────────────────────────────────────────────────────────────┘
                              │
            ┌──── opportunity ranking (Phase 4) ────┐
            │ (topic × format) cells, 8 components, │
            │ bootstrap 10–90% interval,            │
            │ ≥1/3 slots by Thompson exploration,   │
            │ own-history Beta priors               │
            └───────────────────────────────────────┘
                              │
   concepts (Phase 5) ─▶ render spec ─▶ TTS + synth audio ─▶ Remotion ─▶ loudnorm ─▶ QC ─▶ human approval ─▶ R2
                              │                                                                │
   own posts + hypotheses ◀───┴──────────── performance feedback (Phase 6) ◀── manual publish ◀┘
```

## Design decisions

**No taxonomy, by construction.** Nothing in discovery, clustering or ranking contains a list of
content categories. Search terms come from three places: the platform's own chart, terms observed
in high-performing videos, and topic-neutral random probes (function words, numerals, single
syllables). Clusters get their labels only *after* clustering, from class-based TF-IDF terms or
from feature deviations. `CLAUDE.md` makes this a repository rule.

**"What" and "how" are separate spaces.** Topic clusters use text (title, description, hashtags,
tags, transcript). Format clusters use measured audiovisual structure (cut rate, time to first
text and first speech, speech ratio, loopability, brightness, and so on). One topic can appear in
many formats, and the reverse. Opportunities are the cells where the two meet. In the synthetic
mechanism test, the two spaces have near-zero agreement with each other's hidden labels
(ARI ≈ 0.02), as intended.

**Velocity is measured, not inferred.** `velocity_vph` and `acceleration` need two or more
snapshots. A single view count only gives `lifetime_vph`, which is labelled as accumulated reach.
Creator-relative outperformance needs a baseline of at least 5 recent uploads from the same
creator.

**Clusters change over time.** Each run clusters the videos inside a lookback window (default 30
days). `lineage()` links each cluster to the previous run's clusters by member overlap and labels
it emerged / persisted / merged / split, or marks old clusters as dissolved.
`popularity_delta_vs_prev` separates rising patterns from declining ones.

**Uncertainty and exploration.** Ranking scores are bootstrapped over cluster members. About a
third of the slots are filled by Thompson draws among topics not already chosen, so the engine
keeps testing outside what has already worked. Our own results enter as Beta posteriors per
(topic|format) cell.

**Persistence.** Every write goes to a local SQLite store *and* a sync queue. `run_job` drains the
queue to Supabase (PostgREST upsert, `on_conflict` = primary key) after each job. A row stays
queued until it has been pushed. `dgl pull` restores the working set into a fresh runner or
session. Media goes to R2 on approval.

**Human approval gate.** A production gets `awaiting_approval` only if every blocking QC check
passes. `dgl approve` is the only path to `approved`. Nothing in the codebase posts to a platform.

## Recurring operation

| Cadence | Job | Runner |
|---|---|---|
| every 6 h | `dgl pull && dgl snapshot` | GitHub Actions (`.github/workflows/growth-lab-schedule.yml`) |
| daily 06:41 KST | `dgl pull && dgl discover && dgl cluster` | GitHub Actions |
| daily | `/cluster-trends` labels + `dgl rank`, then `/generate-concepts` | Claude Code cloud scheduled task (fresh session per run) |
| on demand | `/produce-video` → human `dgl approve` | Claude Code session |
| daily | `/performance-review` | Claude Code cloud scheduled task |

GitHub Actions handles the deterministic data jobs. Claude Code scheduled sessions handle the
judgement steps through skills. Both read and write the same Supabase schema, so neither depends
on a session's disk.

Logging, retries, deduplication, rate limiting and failure recovery are implemented in:
`dgl.http` (retries with backoff, `Retry-After`, rate limiter), `dgl.pipeline.jobs` (job records
with attempts/cost/error, sync after every job), `Store.insert_snapshot_if_new` (deduplication),
the primary-key upserts everywhere (idempotent re-runs), and `QuotaBudget` / `maxTotalChargeUsd`
(cost caps).

## Module map

| Path | Role |
|---|---|
| `src/dgl/sources/` | YouTube Data API v3 and Apify clients, plus normalisation to rows |
| `src/dgl/discovery/` | query planner (explore/exploit), metrics, discovery and snapshot jobs |
| `src/dgl/analysis/media.py` | FFmpeg/Tesseract inspection of authorized local media |
| `src/dgl/clustering/` | embeddings, density clustering, labels, lineage, cluster job |
| `src/dgl/ranking/opportunity.py` | scoring components, bootstrap, exploration |
| `src/dgl/creative/concepts.py` | concept schema, evidence validator, Claude API generator |
| `src/dgl/render/build.py` + `render/` | narration, original audio, Remotion composition, loudnorm, SRT |
| `src/dgl/qc/video_qc.py` | pre-approval checks |
| `src/dgl/feedback/performance.py` | own-post metrics, hypothesis review, priors |
| `src/dgl/sinks.py`, `src/dgl/store.py` | SQLite working store, Supabase sync/pull, R2 upload |
| `src/dgl/integrations.py` | live WORKING / BLOCKED / NOT TESTED checks |
| `src/dgl/synthetic.py` | labelled synthetic corpora for tests and demos only |
