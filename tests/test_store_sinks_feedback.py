import json

from dgl.config import Settings
from dgl.feedback.performance import review
from dgl.sinks import SupabaseSink


def test_snapshot_dedupe_and_sync_queue(store):
    row = {"platform": "youtube", "video_id": "x", "observed_at": "2026-10-31T00:00:00Z", "views": 1, "likes": 0, "comments": 0}
    assert store.insert_snapshot_if_new(row)
    assert not store.insert_snapshot_if_new({**row, "observed_at": "2026-10-31T01:00:00Z"})
    assert store.insert_snapshot_if_new({**row, "observed_at": "2026-10-31T02:00:00Z", "views": 5})
    assert len(store.pending_sync()) == 2


def test_supabase_drain_upserts_with_conflict_keys(store, monkeypatch):
    calls = []

    class Resp:
        status_code = 201

    def fake_request(self, method, url, **kw):
        calls.append((method, url, kw))
        return Resp()

    monkeypatch.setattr("dgl.http.Client.request", fake_request)
    store.upsert("videos", {"platform": "youtube", "video_id": "a", "url": "u"})
    store.upsert("videos", {"platform": "youtube", "video_id": "a", "url": "u2"})  # dedup within batch
    sink = SupabaseSink(Settings(supabase_url="https://p.supabase.co", supabase_key="k", supabase_schema="growth_lab"))
    assert sink.drain(store) == 1
    method, url, kw = calls[0]
    assert url.endswith("/rest/v1/videos") and kw["params"]["on_conflict"] == "platform,video_id"
    assert kw["headers"]["Content-Profile"] == "growth_lab"
    assert json.loads(kw["data"])[0]["url"] == "u2"
    assert store.pending_sync() == []


def test_feedback_review_separates_reach_and_quality(store):
    for i, (views, follows) in enumerate([(1000, 1), (5000, 2), (3000, 30)]):
        store.upsert("own_posts", {"post_id": f"p{i}", "production_id": "x", "concept_id": "c", "platform": "youtube",
                                   "platform_video_id": str(i), "url": "u", "published_at": "2026-10-31",
                                   "hypothesis": {"topic_cluster": "t0", "format_cluster": "f1" if i < 2 else "f2",
                                                  "predicted_views_range": [2000, 6000], "statement": "h"}})
        store.upsert("own_snapshots", {"post_id": f"p{i}", "observed_at": "2026-11-01", "views": views, "reach": None,
                                       "likes": 1, "comments": 0, "shares": 2, "saves": 1, "avg_view_pct": 80,
                                       "follows": follows, "raw": {}})
    r = review(store)
    assert r["account_median_views"]["youtube"] == 3000
    assert r["cells"]["t0|f1"] == {"wins": 1, "losses": 1, "posterior_mean": 0.5}
    best_quality = max(r["posts"], key=lambda p: p["audience_quality"]["follow_per_1k_views"])
    assert best_quality["post_id"] == "p2" and not best_quality["beat_account_median"]
    assert all(p["evidence_strength"].startswith("observational") for p in r["posts"])


def test_supabase_migration_matches_local_schema():
    import re
    import sqlite3
    from pathlib import Path

    from dgl.store import SCHEMA
    sql = (Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "0001_growth_lab_init.sql").read_text()
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    tables = [t for (t,) in db.execute("select name from sqlite_master where type='table'") if t not in ("sync_queue", "sqlite_sequence")]
    for t in tables:
        m = re.search(rf"create table if not exists growth_lab\.{t} \((.*?)\);", sql, re.S)
        assert m, f"{t} missing from migration"
        for col in (r[1] for r in db.execute(f"pragma table_info({t})")):
            assert re.search(rf"\b{col}\b", m.group(1)), f"{t}.{col} missing from migration"


def test_supabase_pull_restores_without_requeue(store, monkeypatch):
    class Resp:
        def __init__(self, rows):
            self._rows = rows

        def json(self):
            return self._rows

    def fake_request(self, method, url, **kw):
        if url.endswith("/videos") and kw["params"]["offset"] == 0:
            return Resp([{"platform": "youtube", "video_id": "a", "url": "u", "hashtags": ["x"]}])
        return Resp([])

    monkeypatch.setattr("dgl.http.Client.request", fake_request)
    sink = SupabaseSink(Settings(supabase_url="https://p.supabase.co", supabase_key="k"))
    counts = sink.pull(store, tables=("videos", "video_snapshots"))
    assert counts == {"videos": 1, "video_snapshots": 0}
    assert store.query("SELECT video_id FROM videos") == [{"video_id": "a"}]
    assert store.pending_sync() == []


def test_settings_accept_provider_variable_names(monkeypatch):
    for k in ("SUPABASE_SERVICE_ROLE_KEY", "R2_ACCOUNT_ID", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_x")
    monkeypatch.setenv("R2_ENDPOINT", "https://abc.r2.cloudflarestorage.com/")
    monkeypatch.setenv("R2_BUCKET_NAME", "media")
    s = Settings()
    assert s.supabase_key == "sb_secret_x"
    assert s.r2_endpoint_url == "https://abc.r2.cloudflarestorage.com" and s.r2_bucket == "media"
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "legacy")
    assert Settings().supabase_key == "legacy"  # explicit name wins


def test_upsert_video_keeps_first_discovery(store):
    v = {"platform": "youtube", "video_id": "a", "url": "u", "title": "old", "discovered_via": "search:random:x",
         "first_seen_at": "2026-10-31T01:00:00+00:00", "raw": {"categoryId": "22"}}
    assert store.upsert_video(v)
    assert not store.upsert_video({**v, "title": "edited", "discovered_via": "search:exploit:y",
                                   "first_seen_at": "2026-11-01T00:00:00+00:00", "raw": {"categoryId": "24"}})
    row = store.query("SELECT * FROM videos")[0]
    assert row["title"] == "edited"
    assert row["first_seen_at"] == "2026-10-31T01:00:00+00:00" and row["discovered_via"] == "search:random:x"
    import json
    assert json.loads(row["raw"])["seen_via"] == ["search:random:x", "search:exploit:y"]


def test_drain_writes_parents_before_children(store, monkeypatch):
    order = []

    class Resp:
        status_code = 201

    monkeypatch.setattr("dgl.http.Client.request", lambda self, m, url, **kw: (order.append(url.rsplit("/", 1)[1]), Resp())[1])
    store.upsert("clusters", {"run_id": "r1", "cluster_id": "t0"})
    store.upsert("cluster_members", {"run_id": "r2", "cluster_id": "t0", "platform": "p", "video_id": "v"})
    store.upsert("cluster_runs", {"run_id": "r2"})
    SupabaseSink(Settings(supabase_url="https://p.supabase.co", supabase_key="k")).drain(store)
    assert order == ["cluster_runs", "clusters", "cluster_members"]
