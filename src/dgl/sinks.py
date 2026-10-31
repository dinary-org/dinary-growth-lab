"""Durable sinks: Supabase (PostgREST) for research rows, Cloudflare R2 for media."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from .config import Settings
from .http import Client
from .store import PRIMARY_KEYS, Store

log = logging.getLogger("dgl.sinks")


class SupabaseSink:
    """Replicates the local sync queue into Supabase via PostgREST upserts."""

    def __init__(self, s: Settings):
        if not (s.supabase_url and s.supabase_key):
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        self.base = s.supabase_url.rstrip("/") + "/rest/v1"
        self.schema = s.supabase_schema
        self.http = Client("supabase", min_interval_s=0.05)
        self.headers = {
            "apikey": s.supabase_key,
            "Authorization": f"Bearer {s.supabase_key}",
            "Content-Type": "application/json",
            "Content-Profile": self.schema,
            "Accept-Profile": self.schema,
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }

    def upsert(self, table: str, rows: list[dict]) -> None:
        on_conflict = ",".join(PRIMARY_KEYS[table])
        for i in range(0, len(rows), 200):
            self.http.request(
                "POST",
                f"{self.base}/{table}",
                params={"on_conflict": on_conflict},
                headers=self.headers,
                data=json.dumps(rows[i : i + 200], ensure_ascii=False, default=str).encode(),
            )

    def pull(self, store: Store, tables: tuple[str, ...] = ("videos", "video_snapshots", "creators", "analyses",
                                                           "cluster_runs", "clusters", "cluster_members", "own_posts",
                                                           "own_snapshots"), page: int = 1000) -> dict[str, int]:
        """Restore the working set into a fresh (ephemeral) local store. Pulled rows are not re-queued."""
        counts = {}
        for table in tables:
            n, offset = 0, 0
            while True:
                rows = self.http.request("GET", f"{self.base}/{table}", headers=self.headers,
                                         params={"select": "*", "limit": page, "offset": offset}).json()
                store.upsert_many(table, rows, replicate=False)
                n += len(rows)
                offset += page
                if len(rows) < page:
                    break
            counts[table] = n
        return counts

    def drain(self, store: Store) -> int:
        """Push queued rows. Rows stay queued until acknowledged, so a failed drain is safe to retry."""
        total = 0
        while batch := store.pending_sync():
            by_table: dict[str, dict[tuple, dict]] = defaultdict(dict)
            for item in batch:
                row = json.loads(item["row"])
                key = tuple(row.get(k) for k in PRIMARY_KEYS[item["tbl"]])
                by_table[item["tbl"]][key] = row  # last write wins within a batch
            for table, rows in by_table.items():
                self.upsert(table, list(rows.values()))
                total += len(rows)
            store.ack_sync(batch[-1]["seq"])
        return total


class R2Sink:
    """Uploads files to Cloudflare R2 through its S3-compatible endpoint."""

    def __init__(self, s: Settings):
        if not (s.r2_endpoint_url and s.r2_access_key_id and s.r2_secret_access_key):
            raise RuntimeError("R2_ENDPOINT (or R2_ACCOUNT_ID), R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY are required")
        import boto3  # optional dependency

        self.bucket = s.r2_bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=s.r2_endpoint_url,
            aws_access_key_id=s.r2_access_key_id,
            aws_secret_access_key=s.r2_secret_access_key,
            region_name="auto",
        )

    def put(self, path: Path, key: str, content_type: str = "video/mp4") -> str:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        return f"r2://{self.bucket}/{key}"
