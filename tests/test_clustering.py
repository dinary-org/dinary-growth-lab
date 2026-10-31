from datetime import timedelta

from sklearn.metrics import adjusted_rand_score

from dgl import synthetic as S
from dgl.clustering.emergent import lineage
from dgl.clustering.run import run_clustering
from dgl.store import loads


def _truth(store, res, space, key):
    t, p = [], []
    for c in res["spaces"][space]["clusters"]:
        for m in c["members"]:
            raw = store.query("SELECT raw FROM videos WHERE video_id=?", (m["video_id"],))[0]["raw"]
            t.append(loads(raw)["_truth"][key])
            p.append(c["cluster_id"])
    return adjusted_rand_score(t, p)


def test_topics_recovered_without_taxonomy_and_independent_of_format(store):
    rows, _ = S.corpus(7, {"A": 12, "B": 12, "C": 12}, S.NOW - timedelta(days=10), 9)
    S.load(store, rows)
    res = run_clustering(store, now=S.NOW)
    assert _truth(store, res, "topic", "topic") > 0.9
    assert _truth(store, res, "format", "format") > 0.5
    assert abs(_truth(store, res, "format", "topic")) < 0.2  # "how" is not "what"
    labels = [c["label"] for c in res["spaces"]["topic"]["clusters"]]
    assert all(l for l in labels)  # labels produced after clustering


def test_lineage_across_runs_emerge_and_dissolve(store):
    old, voc = S.corpus(1, {"A": 10, "B": 10}, S.NOW - timedelta(days=28), 10, prefix="a")
    gone, voc = S.corpus(2, {"C": 10}, S.NOW - timedelta(days=40), 6, vocabs=voc, prefix="b")
    S.load(store, old + gone)
    r1 = run_clustering(store, lookback_days=40, now=S.NOW - timedelta(days=4))
    assert len([c for c in r1["spaces"]["topic"]["clusters"] if not c["cluster_id"].endswith("noise")]) == 3
    new, voc = S.corpus(3, {"A": 5, "D": 10}, S.NOW - timedelta(days=3), 3, vocabs=voc, prefix="c")
    S.load(store, new)
    r2 = run_clustering(store, lookback_days=30, now=S.NOW)
    events = [c["lineage"]["event"] for c in r2["spaces"]["topic"]["clusters"]]
    assert events.count("persisted") == 2 and events.count("emerged") == 1
    assert len(r2["spaces"]["topic"]["lineage"]["dissolved"]) == 1


def test_lineage_merge_and_split():
    prev = {"p1": {"a", "b", "c"}, "p2": {"d", "e", "f"}, "p3": {"g", "h", "i", "j"}}
    cur = {"c1": {"a", "b", "d", "e"}, "c2": {"g", "h"}, "c3": {"i", "j"}, "c4": {"x", "y"}}
    lin = lineage(prev, cur)
    assert lin["current"]["c1"]["event"] == "merged"
    assert lin["current"]["c2"]["event"] == "split" and lin["current"]["c3"]["event"] == "split"
    assert lin["current"]["c4"]["event"] == "emerged"


def test_too_few_videos_is_skipped(store):
    rows, _ = S.corpus(1, {"A": 3}, S.NOW - timedelta(days=2), 1)
    S.load(store, rows)
    assert run_clustering(store, now=S.NOW)["status"] == "skipped"


def test_cluster_stats_reports_query_echo_and_ads():
    from dgl.clustering.emergent import cluster_stats
    members = [{"creator_id": f"c{i}", "discovered_via": "search:exploit:#x" if i < 8 else "chart", "popularity": 0.5,
                "ad_suspect": i == 0, "log_outperformance": 3.0 if i == 0 else 0.1} for i in range(10)]
    st = cluster_stats(members)
    assert st["query_concentration"] == 0.8 and st["dominant_discovery_path"] == "search:exploit:#x"
    assert st["n_ad_suspect"] == 1 and st["median_log_outperformance"] == 0.1  # ad excluded from evidence
