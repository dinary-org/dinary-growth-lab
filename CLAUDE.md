# DINARY Growth Lab — working rules

Autonomous research → creation engine for organic short-form reach (Korea first). Read
`docs/ARCHITECTURE.md` and the skills in `.claude/skills/` before changing the pipeline.

Non-negotiable:
- **Real data only.** Never fabricate videos, metrics, transcripts or analysis. Missing data is
  recorded as missing. Synthetic data lives only in `src/dgl/synthetic.py` and tests/demos, and is
  labelled SYNTHETIC wherever it appears.
- **No predefined content categories.** Do not add niche lists, category weights or DINARY/food
  preferences to discovery, clustering or ranking.
- **Access levels.** Every analysis states `metadata_only`, `visual` or `full_av` and its rights basis.
  Never bypass authentication or download platform media against terms.
- **Facts vs hypotheses.** Concepts and reports keep measured facts, secondary claims and hypotheses separate.
- **Human approval** before anything is published. The code never posts to a platform.
- **Persistence.** The cloud filesystem is ephemeral: research rows go to Supabase (`growth_lab`
  schema), media to R2. `data/` and `work/` are git-ignored scratch.

Commands: `bash scripts/setup.sh`, `.venv/bin/dgl --help`, `.venv/bin/python -m pytest -q`,
`cd render && npx tsc -p .`.
