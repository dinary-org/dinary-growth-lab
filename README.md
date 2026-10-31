# DINARY Growth Lab: autonomous short-form content engine

An evidence-based system that finds what people watch on YouTube Shorts and Instagram Reels,
learns *why* it works without a predefined content taxonomy, and produces original Korean
9:16 videos. Nothing is published without human approval.

> **Status (2026-10-31):** the pipeline is implemented, has 38 tests passing, and produced one
> rendered video that passes QC. Real platform collection is **blocked pending credentials and
> network access**. See [`reports/2026-10-31-milestone-1.md`](reports/2026-10-31-milestone-1.md)
> and [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md).

## Quick start (Claude Code Cloud or any Linux box)

```bash
bash scripts/setup.sh
```
```bash
cp .env.example .env
```
```bash
.venv/bin/dgl status
```

In Claude Code Cloud, put secrets in the cloud environment's settings, not in `.env`.

| Stage | Command | Skill |
|---|---|---|
| Integration check | `dgl status` | `/lab-status` |
| Discovery (Phase 1) | `dgl discover --platform youtube\|instagram\|all` | `/discover-trends` |
| Repeated snapshots | `dgl snapshot` | `/discover-trends` |
| Video analysis (Phase 2) | `dgl analyze FILE --rights "..."` | `/analyze-video` |
| Emergent clustering (Phase 3) | `dgl cluster --out ...` | `/cluster-trends` |
| Opportunities (Phase 4) | `dgl rank --clusters ... --out ...` | `/cluster-trends` |
| Concepts (Phase 5) | `dgl concepts` (validator) | `/generate-concepts` |
| Render + QC (Phase 5) | `dgl render SPEC` | `/produce-video` |
| Approval gate | `dgl approve PRODUCTION_ID --by NAME` | `/produce-video` |
| Feedback (Phase 6) | `dgl post-register ...`, `dgl feedback` | `/performance-review` |
| Persistence (Phase 7) | `dgl sync`, `dgl pull` | (automatic after every job) |

## Repository layout

```
src/dgl/            Python package (see docs/ARCHITECTURE.md for the module map)
render/             Remotion project: data-driven vertical Timeline composition
.claude/skills/     project skills for each pipeline stage
supabase/           growth_lab schema migration
research/           secondary (unverified) evidence, clearly labelled
outputs/            concepts, render specs, rendered video + SRT + QC, analyses, demos
reports/            research reports
tests/              pytest suite (sources, discovery, clustering, ranking, concepts, media/QC, sinks)
scripts/            setup and the synthetic clustering demo
```

## Principles enforced in code

- **No predefined categories.** The query planner uses the platform's chart, terms observed in
  the corpus, and topic-neutral random probes, with at least 35% exploration. Clusters are
  labelled after they are found.
- **Real data only.** Missing metrics are stored as missing. Synthetic corpora exist only for
  tests and demos and are labelled SYNTHETIC.
- **Access levels.** Every analysis records `metadata_only` / `visual` / `full_av` and a rights basis.
- **Traceability.** Clusters keep their member videos, opportunities keep their evidence, and the
  concept validator rejects references that don't exist in the store.
- **Uncertainty.** Opportunity scores carry bootstrap intervals, and some slots are reserved for
  exploration.
- **Human approval** before publishing. The code never posts.

## Tests

```bash
.venv/bin/python -m pytest -q
```
```bash
cd render && npx tsc -p .
```
```bash
.venv/bin/python scripts/demo_synthetic_clustering.py
```
