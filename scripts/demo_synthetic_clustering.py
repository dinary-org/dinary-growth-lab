"""Mechanism demo on a SYNTHETIC corpus (random-syllable vocabularies, hidden labels).

Shows that (1) topic and format clusters are recovered without a predefined
taxonomy, (2) the two spaces are independent ("what" vs "how"), and (3)
clusters emerge / persist / dissolve across runs as the lookback window moves.
Outputs are written to outputs/demo/ and are NOT research findings.
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

from sklearn.metrics import adjusted_rand_score

from dgl import synthetic as S
from dgl.clustering.run import run_clustering
from dgl.store import Store, loads

OUT = Path(__file__).resolve().parents[1] / "outputs" / "demo"


def score(store: Store, res: dict, space: str, truth_key: str) -> float:
    truth, pred = [], []
    for c in res["spaces"][space]["clusters"]:
        for m in c["members"]:
            v = store.query("SELECT raw FROM videos WHERE platform=? AND video_id=?", (m["platform"], m["video_id"]))[0]
            truth.append(loads(v["raw"])["_truth"][truth_key])
            pred.append(c["cluster_id"])
    return adjusted_rand_score(truth, pred)


def main() -> int:
    store = Store(":memory:")
    t0 = S.NOW - timedelta(days=40)
    # window 1 material: T1, T2 (continue later), T3 (old, will age out)
    old, vocabs = S.corpus(1, {"T1": 12, "T2": 12}, S.NOW - timedelta(days=28), 14, prefix="a")
    gone, vocabs = S.corpus(2, {"T3": 10}, t0, 8, vocabs=vocabs, prefix="b")
    S.load(store, old + gone)
    run1 = run_clustering(store, lookback_days=40, now=S.NOW - timedelta(days=5))
    # window 2 material: more T1/T2, and a new topic T4
    new, vocabs = S.corpus(3, {"T1": 6, "T2": 6, "T4": 10}, S.NOW - timedelta(days=8), 8, vocabs=vocabs, prefix="c")
    S.load(store, new)
    run2 = run_clustering(store, lookback_days=30, now=S.NOW)

    summary = {"_label": "SYNTHETIC MECHANISM DEMO — not research data", "runs": []}
    for name, res in (("run1", run1), ("run2", run2)):
        r = {"run": name, "n_videos": res["n_videos"]}
        for space, key in (("topic", "topic"), ("format", "format")):
            sp = res["spaces"][space]
            r[space] = {"method": sp["method"], "silhouette": sp["silhouette"],
                        "ari_vs_hidden_topic": round(score(store, res, space, "topic"), 3),
                        "ari_vs_hidden_format": round(score(store, res, space, "format"), 3),
                        "clusters": [{"id": c["cluster_id"], "label": c["label"], "size": c["stats"]["size"],
                                      "lineage": c["lineage"]["event"], "from": c["lineage"]["from"]} for c in sp["clusters"]],
                        "dissolved": sp["lineage"]["dissolved"]}
        summary["runs"].append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "synthetic_clustering_demo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
