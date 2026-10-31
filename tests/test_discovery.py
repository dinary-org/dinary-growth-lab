from datetime import datetime, timezone

from dgl.discovery.explore import NEUTRAL_PROBES, QueryPlanner
from dgl.discovery.metrics import popularity_score, video_metrics

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
V = {"published_at": "2026-10-30T12:00:00Z"}


def test_velocity_requires_two_snapshots():
    one = video_metrics(V, [{"observed_at": "2026-10-31T00:00:00Z", "views": 1000}], None, NOW)
    assert one["velocity_vph"] is None and one["lifetime_vph"] is not None
    two = video_metrics(V, [{"observed_at": "2026-10-31T00:00:00Z", "views": 1000},
                            {"observed_at": "2026-10-31T10:00:00Z", "views": 3000}], None, NOW)
    assert two["velocity_vph"] == 200


def test_acceleration_sign():
    snaps = [{"observed_at": f"2026-10-31T0{h}:00:00Z", "views": v} for h, v in ((0, 0), (2, 100), (4, 400))]
    assert video_metrics(V, snaps, None, NOW)["acceleration"] > 0


def test_creator_relative_needs_baseline():
    snaps = [{"observed_at": "2026-10-31T00:00:00Z", "views": 10000}]
    assert video_metrics(V, snaps, {"baseline_median_views": 1000, "baseline_n": 3}, NOW)["creator_relative"] is None
    m = video_metrics(V, snaps, {"baseline_median_views": 1000, "baseline_n": 10}, NOW)
    assert m["creator_relative"] == 10
    assert popularity_score(m) > popularity_score({"views": 10000})


def test_planner_reserves_exploration_and_learns_from_corpus():
    p = QueryPlanner(explore_share=0.35, seed=1)
    vids = [({"title": "알파 베타", "hashtags": ["감마"]}, 0.9), ({"title": "알파 델타", "hashtags": ["감마"]}, 0.8),
            ({"title": "엡실론", "hashtags": []}, 0.1)]
    p.update_corpus(vids)
    plan = p.plan(6, "ko")
    reasons = [r for _, r in plan]
    assert len(plan) == 6
    assert reasons.count("exploit") >= 1
    assert reasons.count("explore") + reasons.count("random") >= 2
    assert any(t in NEUTRAL_PROBES["ko"] for t, r in plan if r == "random")
    assert ("알파", "exploit") in plan


def test_planner_cold_start_is_pure_exploration():
    plan = QueryPlanner(seed=0).plan(4, "ko")
    assert all(r == "random" for _, r in plan)
