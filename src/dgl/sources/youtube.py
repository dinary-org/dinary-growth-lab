"""YouTube Data API v3 discovery client.

Only official endpoints are used. Quota costs (units) follow Google's published
table: search.list = 100, videos.list / channels.list / playlistItems.list = 1.
A `QuotaBudget` stops collection before the configured daily budget is spent.

Shorts are not flagged by the API. We treat a video as a short-form candidate
when its duration is <= 180 s (YouTube's current Shorts limit) and record that
this is an inference (`shorts_inferred_from_duration`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterator

from ..http import Client
from ..store import now_iso

API = "https://www.googleapis.com/youtube/v3"
COST = {"search": 100, "videos": 1, "channels": 1, "playlistItems": 1, "videoCategories": 1}
SHORTS_MAX_S = 180
HASHTAG_RE = re.compile(r"#([\w가-힣]+)", re.UNICODE)


class QuotaExceeded(RuntimeError):
    pass


@dataclass
class QuotaBudget:
    limit: int
    used: int = 0
    log: list[tuple[str, int]] = field(default_factory=list)

    def spend(self, endpoint: str) -> None:
        cost = COST[endpoint]
        if self.used + cost > self.limit:
            raise QuotaExceeded(f"quota budget {self.limit} would be exceeded by {endpoint}")
        self.used += cost
        self.log.append((endpoint, cost))


def parse_iso8601_duration(s: str | None) -> float | None:
    if not s:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", s)
    if not m:
        return None
    d, h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class YouTubeSource:
    platform = "youtube"

    def __init__(self, api_key: str, budget: QuotaBudget, region: str = "KR", language: str = "ko"):
        if not api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")
        self.key = api_key
        self.budget = budget
        self.region = region
        self.language = language
        self.http = Client("youtube", min_interval_s=0.1)

    def _get(self, endpoint: str, **params: Any) -> dict:
        self.budget.spend(endpoint)
        params["key"] = self.key
        return self.http.get_json(f"{API}/{endpoint}", params=params)

    # --- discovery signals -------------------------------------------------
    def most_popular_ids(self, pages: int = 4, category_id: str | None = None) -> list[str]:
        """Platform-wide chart for the region: a niche-free popularity signal."""
        ids, token = [], None
        for _ in range(pages):
            params: dict[str, Any] = dict(
                part="id", chart="mostPopular", regionCode=self.region, maxResults=50
            )
            if category_id:
                params["videoCategoryId"] = category_id
            if token:
                params["pageToken"] = token
            data = self._get("videos", **params)
            ids += [it["id"] for it in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                break
        return ids

    def search_short_ids(
        self, q: str, order: str = "viewCount", days: int = 14, pages: int = 1, lang: str | None = None
    ) -> list[str]:
        after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids, token = [], None
        for _ in range(pages):
            params: dict[str, Any] = dict(
                part="id", type="video", videoDuration="short", q=q, order=order,
                publishedAfter=after, regionCode=self.region, maxResults=50,
            )
            if lang:
                params["relevanceLanguage"] = lang
            if token:
                params["pageToken"] = token
            data = self._get("search", **params)
            ids += [it["id"]["videoId"] for it in data.get("items", []) if it["id"].get("videoId")]
            token = data.get("nextPageToken")
            if not token:
                break
        return ids

    # --- hydration ---------------------------------------------------------
    def videos(self, ids: list[str]) -> Iterator[dict]:
        for i in range(0, len(ids), 50):
            data = self._get(
                "videos", part="snippet,statistics,contentDetails,status", id=",".join(ids[i : i + 50])
            )
            yield from data.get("items", [])

    def channels(self, ids: list[str]) -> Iterator[dict]:
        for i in range(0, len(ids), 50):
            data = self._get("channels", part="snippet,statistics,contentDetails", id=",".join(ids[i : i + 50]))
            yield from data.get("items", [])

    def recent_upload_views(self, uploads_playlist: str, n: int = 30, shorts_only: bool = True) -> list[int]:
        """View counts of a creator's recent uploads, for creator-relative baselines.

        With `shorts_only`, long-form uploads are excluded so a Short is compared with
        the creator's other Shorts rather than with a different format's audience.
        """
        data = self._get("playlistItems", part="contentDetails", playlistId=uploads_playlist, maxResults=min(n, 50))
        vids = [it["contentDetails"]["videoId"] for it in data.get("items", [])]
        out = []
        for it in self.videos(vids):
            d = parse_iso8601_duration(it.get("contentDetails", {}).get("duration"))
            if shorts_only and (d is None or d > SHORTS_MAX_S):
                continue
            if (v := _int(it.get("statistics", {}).get("viewCount"))) is not None:
                out.append(v)
        return out

    # --- normalisation -----------------------------------------------------
    @staticmethod
    def to_rows(item: dict, discovered_via: str) -> tuple[dict, dict]:
        sn, st, cd = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
        duration = parse_iso8601_duration(cd.get("duration"))
        text = f"{sn.get('title', '')} {sn.get('description', '')}"
        video = {
            "platform": "youtube",
            "video_id": item["id"],
            "url": f"https://www.youtube.com/shorts/{item['id']}"
            if duration is not None and duration <= SHORTS_MAX_S
            else f"https://www.youtube.com/watch?v={item['id']}",
            "creator_id": sn.get("channelId"),
            "creator_name": sn.get("channelTitle"),
            "published_at": sn.get("publishedAt"),
            "title": sn.get("title"),
            "description": sn.get("description"),
            "hashtags": sorted(set(h.lower() for h in HASHTAG_RE.findall(text))),
            "tags": sn.get("tags", []),
            "duration_s": duration,
            "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
            "discovered_via": discovered_via,
            "first_seen_at": now_iso(),
            "raw": {"categoryId": sn.get("categoryId"), "liveBroadcastContent": sn.get("liveBroadcastContent"),
                    "shorts_inferred_from_duration": True},
        }
        missing = [k for k, src in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))
                   if src not in st]
        snap = {
            "platform": "youtube",
            "video_id": item["id"],
            "observed_at": now_iso(),
            "views": _int(st.get("viewCount")),
            "likes": _int(st.get("likeCount")),
            "comments": _int(st.get("commentCount")),
            "shares": None,
            "saves": None,
            "source": "youtube_data_api_v3",
            # shares/saves are never exposed publicly by the Data API
            "missing": missing + ["shares", "saves"],
        }
        return video, snap

    @staticmethod
    def channel_row(item: dict, recent_views: list[int] | None) -> dict:
        st = item.get("statistics", {})
        return {
            "platform": "youtube",
            "creator_id": item["id"],
            "name": item.get("snippet", {}).get("title"),
            "followers": None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
            "total_views": _int(st.get("viewCount")),
            "video_count": _int(st.get("videoCount")),
            "baseline_median_views": median(recent_views) if recent_views else None,
            # None = never measured; 0..4 = measured but too few recent Shorts for a baseline
            "baseline_n": None if recent_views is None else len(recent_views),
            "observed_at": now_iso(),
            "raw": {"uploads": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")},
        }
