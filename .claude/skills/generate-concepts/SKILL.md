---
name: generate-concepts
description: Turn ranked opportunities into original Korean short-form video concepts (script, 3+ hooks, storyboard, edit/audio plan, assets, cost) that cite stored evidence and pass the validator. Use after rank, or when asked for video ideas.
---

# Generate concepts

1. Inputs: `outputs/opportunities/<date>.json` (measured) and, only when no measured data exists,
   `research/secondary_evidence.json` (must be cited as `kind: "secondary"` and the concept marked
   PROVISIONAL in `why_now`).
2. For each selected opportunity write one JSON file to `outputs/concepts/<concept_id>.json`
   matching `CONCEPT_SCHEMA` in `src/dgl/creative/concepts.py`:
   - transfer the *mechanism* only — never copy footage, script, jokes, characters or music;
   - natural spoken Korean (구어체); at least 3 hook alternatives;
   - `measured_facts` only for numbers that exist in the store; everything else in `hypotheses`,
     each falsifiable with a named metric;
   - vary formats across concepts; keep ≥1 exploration concept.
3. Validate: `.venv/bin/dgl concepts` must print `OK` for every file (non-zero exit otherwise).
4. Unattended alternative (needs `ANTHROPIC_API_KEY`): `generate_with_claude()` in the same module.
