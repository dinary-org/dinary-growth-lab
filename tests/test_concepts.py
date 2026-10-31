import copy
import json
from pathlib import Path

from dgl.creative.concepts import CONCEPT_SCHEMA, validate

ROOT = Path(__file__).resolve().parents[1]
SEC = {e["id"] for e in json.loads((ROOT / "research" / "secondary_evidence.json").read_text())["items"]}


def _concepts():
    return [json.loads(p.read_text()) for p in sorted((ROOT / "outputs" / "concepts").glob("*.json"))]


def test_repo_concepts_are_valid_and_diverse():
    cs = _concepts()
    assert len(cs) >= 3
    for c in cs:
        assert validate(c, set(), SEC) == [], c["concept_id"]
        assert set(CONCEPT_SCHEMA["required"]) <= set(c)
    assert len({c["format"] for c in cs}) == len(cs)  # not one template
    assert any("EXPLORATION" in c["logline_en"] for c in cs)


def test_validator_rejects_unsupported_claims():
    c = copy.deepcopy(_concepts()[0])
    c["evidence"] = [{"ref": "youtube:NOPE", "kind": "measured", "claim": "x"}]
    c["measured_facts"] = ["something measured"]
    probs = validate(c, set(), SEC)
    assert any("not found" in p for p in probs)
    c["evidence"] = []
    assert any("no evidence" in p for p in validate(c, set(), SEC))


def test_validator_checks_storyboard_and_hooks():
    c = copy.deepcopy(_concepts()[0])
    c["hooks_ko"] = c["hooks_ko"][:1]
    c["storyboard"][1]["start_s"] = 0.1
    probs = validate(c, set(), SEC)
    assert any("hook" in p for p in probs) and any("overlap" in p for p in probs)


def test_schema_is_strict_structured_output_compatible():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        elif node.get("type") == "array":
            walk(node["items"])
    walk(CONCEPT_SCHEMA)
