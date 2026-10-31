"""Persistence layer.

A local SQLite database is the working copy inside a session; every write is
also queued for replication to Supabase (the durable store) when credentials
are configured. The cloud-session filesystem is ephemeral, so `sync()` must
run before a session ends — the scheduled jobs do this automatically.

Tables mirror `supabase/migrations/0001_init.sql`. JSON columns hold
structured payloads; scalar columns exist for anything we filter or rank on.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  platform TEXT NOT NULL, video_id TEXT NOT NULL, url TEXT NOT NULL,
  creator_id TEXT, creator_name TEXT, published_at TEXT, title TEXT,
  description TEXT, hashtags TEXT, tags TEXT, duration_s REAL, language TEXT,
  discovered_via TEXT, first_seen_at TEXT, raw TEXT,
  PRIMARY KEY (platform, video_id));
CREATE TABLE IF NOT EXISTS video_snapshots (
  platform TEXT NOT NULL, video_id TEXT NOT NULL, observed_at TEXT NOT NULL,
  views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER,
  source TEXT, missing TEXT,
  PRIMARY KEY (platform, video_id, observed_at));
CREATE TABLE IF NOT EXISTS creators (
  platform TEXT NOT NULL, creator_id TEXT NOT NULL, name TEXT, followers INTEGER,
  total_views INTEGER, video_count INTEGER, baseline_median_views REAL,
  baseline_n INTEGER, observed_at TEXT, raw TEXT,
  PRIMARY KEY (platform, creator_id));
CREATE TABLE IF NOT EXISTS analyses (
  analysis_id TEXT PRIMARY KEY, platform TEXT, video_id TEXT, access_level TEXT,
  media_source TEXT, rights_basis TEXT, created_at TEXT, features TEXT, observations TEXT);
CREATE TABLE IF NOT EXISTS cluster_runs (
  run_id TEXT PRIMARY KEY, created_at TEXT, space TEXT, params TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS clusters (
  run_id TEXT, cluster_id TEXT, space TEXT, label TEXT, explanation TEXT,
  size INTEGER, n_creators INTEGER, momentum REAL, lineage TEXT, stats TEXT,
  PRIMARY KEY (run_id, cluster_id));
CREATE TABLE IF NOT EXISTS cluster_members (
  run_id TEXT, cluster_id TEXT, platform TEXT, video_id TEXT, weight REAL,
  PRIMARY KEY (run_id, cluster_id, platform, video_id));
CREATE TABLE IF NOT EXISTS opportunities (
  opportunity_id TEXT PRIMARY KEY, run_id TEXT, created_at TEXT, topic_cluster TEXT,
  format_cluster TEXT, score REAL, score_low REAL, score_high REAL, is_exploration INTEGER,
  components TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS concepts (
  concept_id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT, status TEXT,
  title_ko TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS productions (
  production_id TEXT PRIMARY KEY, concept_id TEXT, created_at TEXT, status TEXT,
  video_path TEXT, storage_url TEXT, qc TEXT, approved_by TEXT, approved_at TEXT);
CREATE TABLE IF NOT EXISTS own_posts (
  post_id TEXT PRIMARY KEY, production_id TEXT, concept_id TEXT, platform TEXT,
  platform_video_id TEXT, url TEXT, published_at TEXT, hypothesis TEXT);
CREATE TABLE IF NOT EXISTS own_snapshots (
  post_id TEXT, observed_at TEXT, views INTEGER, reach INTEGER, likes INTEGER,
  comments INTEGER, shares INTEGER, saves INTEGER, avg_view_pct REAL,
  follows INTEGER, raw TEXT, PRIMARY KEY (post_id, observed_at));
CREATE TABLE IF NOT EXISTS integration_checks (
  check_id TEXT PRIMARY KEY, integration TEXT, status TEXT, checked_at TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS job_runs (
  job_id TEXT PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, status TEXT,
  attempts INTEGER, cost TEXT, log TEXT);
CREATE TABLE IF NOT EXISTS sync_queue (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, row TEXT, queued_at TEXT);
"""

PRIMARY_KEYS = {
    "videos": ("platform", "video_id"),
    "video_snapshots": ("platform", "video_id", "observed_at"),
    "creators": ("platform", "creator_id"),
    "analyses": ("analysis_id",),
    "cluster_runs": ("run_id",),
    "clusters": ("run_id", "cluster_id"),
    "cluster_members": ("run_id", "cluster_id", "platform", "video_id"),
    "opportunities": ("opportunity_id",),
    "concepts": ("concept_id",),
    "productions": ("production_id",),
    "own_posts": ("post_id",),
    "own_snapshots": ("post_id", "observed_at"),
    "integration_checks": ("check_id",),
    "job_runs": ("job_id",),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


def _encode(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, bool):
        return int(v)
    return v


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def upsert(self, table: str, row: dict[str, Any], replicate: bool = True) -> None:
        self.upsert_many(table, [row], replicate)

    def upsert_many(self, table: str, rows: Iterable[dict[str, Any]], replicate: bool = True) -> int:
        n = 0
        for row in rows:
            cols = list(row)
            sql = (
                f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})"
            )
            self.db.execute(sql, [_encode(row[c]) for c in cols])
            if replicate:
                self.db.execute(
                    "INSERT INTO sync_queue (tbl,row,queued_at) VALUES (?,?,?)",
                    (table, json.dumps(row, ensure_ascii=False, default=str), now_iso()),
                )
            n += 1
        self.db.commit()
        return n

    def upsert_video(self, row: dict[str, Any]) -> bool:
        """Upsert a video but keep when and how it was *first* discovered; returns True if new.

        Later sightings refresh title/description/tags (creators edit them) and add
        their discovery path to `raw.seen_via`, so discovery-arm yield stays attributable.
        """
        prev = self.query("SELECT first_seen_at, discovered_via, raw FROM videos WHERE platform=? AND video_id=?",
                          (row["platform"], row["video_id"]))
        if not prev:
            self.upsert("videos", row)
            return True
        p = prev[0]
        raw = {**loads(p["raw"], {}), **(row.get("raw") or {})}
        seen = list(dict.fromkeys(loads(p["raw"], {}).get("seen_via", [p["discovered_via"]]) + [row["discovered_via"]]))
        raw["seen_via"] = seen[-20:]
        self.upsert("videos", {**row, "first_seen_at": p["first_seen_at"], "discovered_via": p["discovered_via"], "raw": raw})
        return False

    def insert_snapshot_if_new(self, row: dict[str, Any]) -> bool:
        """Snapshots are append-only; dedupe identical consecutive observations."""
        last = self.db.execute(
            "SELECT views,likes,comments FROM video_snapshots WHERE platform=? AND video_id=? "
            "ORDER BY observed_at DESC LIMIT 1",
            (row["platform"], row["video_id"]),
        ).fetchone()
        if last and (last["views"], last["likes"], last["comments"]) == (
            row.get("views"),
            row.get("likes"),
            row.get("comments"),
        ):
            return False
        self.upsert("video_snapshots", row)
        return True

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def pending_sync(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM sync_queue ORDER BY seq LIMIT ?", (limit,))

    def ack_sync(self, max_seq: int) -> None:
        self.db.execute("DELETE FROM sync_queue WHERE seq<=?", (max_seq,))
        self.db.commit()


def loads(v: Any, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default
