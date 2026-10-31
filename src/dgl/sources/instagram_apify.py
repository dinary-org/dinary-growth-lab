"""Instagram Reels metadata through Apify Store actors.

Apify actors collect *public* metadata only; we never log in, never pass
Instagram session cookies, and never download media through this path.
Field names differ between actor versions, so mapping is defensive and any
absent metric is recorded in `missing` instead of being defaulted to zero.
"""
from __future__ import annotations

import re
from typing import Any

from ..http import Client
from ..store import now_iso

API = "https://api.apify.com/v2"
HASHTAG_RE = re.compile(r"#([\w가-힣]+)", re.UNICODE)


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


class InstagramApifySource:
    platform = "instagram"

    def __init__(self, token: str, reel_actor: str, hashtag_actor: str, max_usd: float):
        if not token:
            raise RuntimeError("APIFY_TOKEN is not set")
        self.token = token
        self.reel_actor = reel_actor
        self.hashtag_actor = hashtag_actor
        self.max_usd = max_usd
        self.http = Client("apify", min_interval_s=1.0, timeout=330)

    def whoami(self) -> dict:
        return self.http.get_json(f"{API}/users/me", params={"token": self.token})["data"]

    def _run(self, actor: str, payload: dict) -> list[dict]:
        return self.http.post_json(
            f"{API}/acts/{actor}/run-sync-get-dataset-items",
            params={"token": self.token, "maxTotalChargeUsd": self.max_usd, "timeout": 300},
            json=payload,
        )

    def hashtag_reels(self, hashtags: list[str], limit: int = 30) -> list[dict]:
        return self._run(self.hashtag_actor, {"hashtags": hashtags, "resultsLimit": limit, "resultsType": "reels"})

    def creator_reels(self, usernames: list[str], limit: int = 20) -> list[dict]:
        return self._run(self.reel_actor, {"username": usernames, "resultsLimit": limit})

    @staticmethod
    def to_rows(item: dict, discovered_via: str) -> tuple[dict, dict] | None:
        code = _first(item, "shortCode", "code")
        if not code:
            return None
        caption = _first(item, "caption") or ""
        tags = item.get("hashtags") or HASHTAG_RE.findall(caption)
        video = {
            "platform": "instagram",
            "video_id": code,
            "url": _first(item, "url") or f"https://www.instagram.com/reel/{code}/",
            "creator_id": _first(item, "ownerId", "ownerUsername"),
            "creator_name": _first(item, "ownerUsername", "ownerFullName"),
            "published_at": _first(item, "timestamp", "takenAt"),
            "title": caption.split("\n")[0][:200],
            "description": caption,
            "hashtags": sorted(set(str(h).lower() for h in tags)),
            "tags": [],
            "duration_s": _first(item, "videoDuration"),
            "language": None,
            "discovered_via": discovered_via,
            "first_seen_at": now_iso(),
            "raw": {k: item.get(k) for k in ("type", "productType", "musicInfo", "locationName") if k in item},
        }
        metrics = {
            "views": _first(item, "videoPlayCount", "videoViewCount", "playCount"),
            "likes": _first(item, "likesCount"),
            "comments": _first(item, "commentsCount"),
        }
        # Instagram hides like counts for some posts; -1 is the actors' sentinel
        metrics = {k: (None if v is None or v == -1 else int(v)) for k, v in metrics.items()}
        snap = {
            "platform": "instagram",
            "video_id": code,
            "observed_at": now_iso(),
            **metrics,
            "shares": None,
            "saves": None,
            "source": "apify",
            "missing": [k for k, v in metrics.items() if v is None] + ["shares", "saves"],
        }
        return video, snap
