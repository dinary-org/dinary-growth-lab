from datetime import timedelta

from dgl import synthetic as S
from dgl.clustering.run import run_clustering
from dgl.ranking.opportunity import OwnHistory, rank


def test_rank_has_intervals_exploration_and_evidence(store):
    rows, _ = S.corpus(11, {"A": 10, "B": 10, "C": 10, "D": 10}, S.NOW - timedelta(days=12), 11)
    S.load(store, rows)
    res = run_clustering(store, now=S.NOW)
    opps = rank(res["spaces"]["topic"]["clusters"], res["spaces"]["format"]["clusters"], res["n_videos"], n_slots=4)
    assert 1 <= len(opps) <= 4
    assert any(o["is_exploration"] for o in opps)
    assert len({o["topic_cluster"] for o in opps}) == len(opps)  # diversity: one per topic
    for o in opps:
        assert o["score_low"] <= o["score_high"]
        assert o["evidence"] and all("video_id" in e for e in o["evidence"])


def test_own_history_shifts_posterior():
    assert OwnHistory(wins=4, losses=0).mean() > OwnHistory().mean() > OwnHistory(wins=0, losses=4).mean()
