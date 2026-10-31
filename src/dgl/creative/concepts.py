"""Concept briefs: schema, evidence validation, and optional Claude API generation.

Concepts can be written two ways:

1. **In a Claude Code session** via the `generate-concepts` skill — Claude reads
   the opportunity report and writes JSON files that must pass `validate()`.
2. **Unattended** via `generate_with_claude()` (needs ANTHROPIC_API_KEY), which
   asks for output constrained to `CONCEPT_SCHEMA`.

Either way, every concept must cite evidence that exists in the store (or in
the secondary-evidence file, explicitly tagged as such), separate measured
facts from hypotheses, and pass the originality checklist.
"""
from __future__ import annotations

import json
from typing import Any

_str = {"type": "string"}
_strs = {"type": "array", "items": _str}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


CONCEPT_SCHEMA = _obj({
    "concept_id": _str,
    "opportunity_id": _str,
    "title_ko": _str,
    "logline_en": _str,
    "evidence": {"type": "array", "items": _obj({
        "ref": _str,            # platform:video_id | secondary:<id> | cluster:<run>/<id>
        "kind": {"type": "string", "enum": ["measured", "secondary", "own_result"]},
        "claim": _str,
    })},
    "measured_facts": _strs,
    "hypotheses": _strs,
    "why_now": _str,
    "creative_principle": _str,   # the reusable mechanism, stated abstractly
    "originality_notes": _str,    # how it avoids copying any source
    "format": _str,
    "duration_s": {"type": "number"},
    "hooks_ko": _strs,            # >= 3 alternatives
    "script_ko": {"type": "array", "items": _obj({"t": {"type": "number"}, "speaker": _str, "line_ko": _str})},
    "storyboard": {"type": "array", "items": _obj({
        "scene": {"type": "integer"}, "start_s": {"type": "number"}, "end_s": {"type": "number"},
        "visual": _str, "on_screen_text_ko": _str, "audio": _str, "edit": _str,
    })},
    "edit_plan": _str,
    "audio_plan": _str,
    "assets_required": {"type": "array", "items": _obj({"asset": _str, "source": _str, "license": _str})},
    "difficulty": {"type": "string", "enum": ["low", "medium", "high"]},
    "est_cost_usd": {"type": "number"},
    "est_hours": {"type": "number"},
    "success_metric": _str,       # what result would confirm / refute the hypothesis
    "risks": _strs,
})


class ConceptInvalid(ValueError):
    pass


def validate(concept: dict, known_refs: set[str], secondary_refs: set[str]) -> list[str]:
    """Return a list of problems; empty list means the concept is acceptable."""
    problems = [f"missing field: {k}" for k in CONCEPT_SCHEMA["required"] if k not in concept]
    if problems:
        return problems
    if len(concept["hooks_ko"]) < 3:
        problems.append("need at least 3 opening-hook alternatives")
    if not concept["evidence"]:
        problems.append("no evidence cited")
    for ev in concept["evidence"]:
        ref = ev["ref"]
        if ev["kind"] == "secondary":
            if ref.removeprefix("secondary:") not in secondary_refs:
                problems.append(f"unknown secondary evidence {ref}")
        elif ev["kind"] in ("measured", "own_result") and ref not in known_refs:
            problems.append(f"evidence {ref} not found in the research store")
    if concept["measured_facts"] and not any(e["kind"] == "measured" for e in concept["evidence"]):
        problems.append("measured_facts listed but no measured evidence cited")
    if not concept["hypotheses"]:
        problems.append("state at least one falsifiable hypothesis")
    sb = concept["storyboard"]
    if not sb or abs(sb[-1]["end_s"] - concept["duration_s"]) > 1.0:
        problems.append("storyboard must cover the stated duration")
    if any(b["start_s"] < a["end_s"] - 0.05 for a, b in zip(sb, sb[1:])):
        problems.append("storyboard scenes overlap")
    if not concept["originality_notes"].strip():
        problems.append("originality notes required")
    return problems


SYSTEM_PROMPT = """You are the creative director of an original Korean short-form video studio.
You receive ranked content opportunities, each with source evidence. Write ONE original
concept for the opportunity given.

Rules:
- Transfer the *mechanism* (hook structure, pacing, payoff) — never the specific footage,
  jokes, characters, music, or script of any source video.
- Cite only evidence refs supplied in the input. Mark claims measured vs secondary.
- Put anything not directly measured under `hypotheses`.
- Natural, spoken Korean (구어체) for the script and hooks; no direct product promotion.
- Production must be feasible for a two-person team with a phone, free/licensed assets,
  optional TTS and optional generative B-roll."""


def generate_with_claude(opportunity: dict, model: str, effort: str = "high") -> dict:
    """Unattended concept generation. Requires `pip install anthropic` and ANTHROPIC_API_KEY."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # re-run on a fallback model if a safety classifier declines
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": CONCEPT_SCHEMA}},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(opportunity, ensure_ascii=False)}],
    )
    if response.stop_reason == "refusal":
        raise ConceptInvalid(f"model declined: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise ConceptInvalid("concept truncated at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
