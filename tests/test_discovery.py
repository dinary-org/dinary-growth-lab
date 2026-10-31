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


def test_planner_never_repeats_a_term():
    for seed in range(20):
        plan = QueryPlanner(seed=seed).plan(8, "ko")
        assert len({t for t, _ in plan}) == len(plan)


def test_baselines_survive_unlistable_channel_and_prioritise_reach(store):
    from dgl.discovery.engine import refresh_baselines
    from dgl.http import HttpError
    from dgl.sources.youtube import QuotaBudget

    for cid, views in (("big", 900), ("hidden", 500), ("small", 10)):
        store.upsert("videos", {"platform": "youtube", "video_id": f"v_{cid}", "url": "u", "creator_id": cid})
        store.upsert("video_snapshots", {"platform": "youtube", "video_id": f"v_{cid}",
                                         "observed_at": "2026-10-31T00:00:00Z", "views": views})

    class FakeYT:
        budget = QuotaBudget(1000)
        channel_row = staticmethod(__import__("dgl.sources.youtube", fromlist=["x"]).YouTubeSource.channel_row)

        def channels(self, ids):
            return [{"id": c, "statistics": {}, "contentDetails": {"relatedPlaylists": {"uploads": f"UU{c}"}}} for c in ids]

        def recent_upload_views(self, uploads):
            if uploads == "UUhidden":
                raise HttpError(404, "playlistItems", "not found")
            return [100, 200, 300, 400, 500]

    out = refresh_baselines(store, FakeYT(), max_creators=2)
    assert out == {"requested": 3, "with_baseline": 1, "unavailable": 1, "errors": []}
    rows = {r["creator_id"]: r for r in store.query("SELECT * FROM creators")}
    assert rows["big"]["baseline_n"] == 5          # top creator processed
    assert rows["hidden"]["baseline_n"] == 0       # recorded, not fatal
    assert rows["small"]["baseline_median_views"] is None  # beyond max_creators: no baseline call


def test_ad_suspects_flagged_and_zero_popularity():
    from dgl.discovery.metrics import flag_ad_suspects
    ms = [{"views": 10_000, "likes": 90, "like_rate": 0.009} for _ in range(30)]
    ad = {"views": 9_000_000, "likes": 0, "like_rate": 0.0}
    small_quiet = {"views": 5_000, "likes": 0, "like_rate": 0.0}  # below the view floor: not flagged
    med = flag_ad_suspects(ms + [ad, small_quiet])
    assert med == 0.009
    assert ad["ad_suspect"] and not small_quiet["ad_suspect"] and not ms[0]["ad_suspect"]
    assert popularity_score(ad) == 0.0 < popularity_score(ms[0])
    assert flag_ad_suspects(ms[:5]) is None  # too few rates to judge
