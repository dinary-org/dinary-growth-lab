"""Own-channel performance tracking and hypothesis review.

Each published post is linked to its production → concept → opportunity, and
stores the hypothesis we made before publishing. Snapshots come from:

* YouTube Analytics API (owner OAuth; views, likes, shares, averageViewPercentage,
  subscribersGained) — reach/impressions are not exposed for Shorts by the API;
* Instagram Graph API media insights (reach, views/plays, likes, comments,
  shares, saved, follows where the account type permits).

`review()` compares outcomes against hypotheses and produces win/loss counts
per (topic|format) cell that feed `ranking.OwnHistory`. It reports
correlations only: one post per cell cannot demonstrate a causal effect, so
causal language is reserved for deliberate A/B tests (`ab_group` on the post).
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from ..http import Client
from ..ranking.opportunity import OwnHistory
from ..store import Store, loads, now_iso

YT_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
IG_GRAPH = "https://graph.facebook.com/v21.0"


def youtube_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    r = Client("google-oauth").post_json("https://oauth2.googleapis.com/token", data={
        "client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token,
        "grant_type": "refresh_token"})
    return r["access_token"]


def youtube_post_metrics(token: str, video_id: str, start: str, end: str) -> dict[str, Any]:
    metrics = "views,likes,comments,shares,averageViewPercentage,averageViewDuration,subscribersGained"
    r = Client("yt-analytics").get_json(YT_ANALYTICS, headers={"Authorization": f"Bearer {token}"}, params={
        "ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics,
        "filters": f"video=={video_id}"})
    row = (r.get("rows") or [[None] * 7])[0]
    keys = metrics.split(",")
    return dict(zip(keys, row))


def instagram_post_metrics(token: str, media_id: str) -> dict[str, Any]:
    r = Client("ig-graph").get_json(f"{IG_GRAPH}/{media_id}/insights", params={
        "metric": "reach,views,likes,comments,shares,saved,total_interactions,follows", "access_token": token})
    return {m["name"]: (m.get("values") or [{}])[0].get("value") for m in r.get("data", [])}


def record_snapshot(store: Store, post_id: str, platform: str, m: dict[str, Any]) -> None:
    store.upsert("own_snapshots", {
        "post_id": post_id, "observed_at": now_iso(),
        "views": m.get("views"), "reach": m.get("reach"), "likes": m.get("likes"), "comments": m.get("comments"),
        "shares": m.get("shares"), "saves": m.get("saved"),
        "avg_view_pct": m.get("averageViewPercentage"),
        "follows": m.get("subscribersGained", m.get("follows")), "raw": {"platform": platform, **m}})


def review(store: Store) -> dict[str, Any]:
    posts = store.query("SELECT * FROM own_posts")
    latest = {}
    for p in posts:
        s = store.query("SELECT * FROM own_snapshots WHERE post_id=? ORDER BY observed_at DESC LIMIT 1", (p["post_id"],))
        if s:
            latest[p["post_id"]] = s[0]
    if not latest:
        return {"status": "no_data", "posts": len(posts), "cells": {}}
    views_by_platform: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        if p["post_id"] in latest and latest[p["post_id"]]["views"] is not None:
            views_by_platform[p["platform"]].append(latest[p["post_id"]]["views"])
    med = {k: median(v) for k, v in views_by_platform.items()}
    cells: dict[str, OwnHistory] = defaultdict(OwnHistory)
    rows = []
    for p in posts:
        s = latest.get(p["post_id"])
        if not s or s["views"] is None:
            continue
        h = loads(p["hypothesis"], {})
        cell = f"{h.get('topic_cluster', '?')}|{h.get('format_cluster', '*')}"
        win = s["views"] > med[p["platform"]]
        if win:
            cells[cell].wins += 1
        else:
            cells[cell].losses += 1
        quality = {  # audience quality, separate from raw reach
            "follow_per_1k_views": round(1000 * (s["follows"] or 0) / s["views"], 3) if s["views"] else None,
            "share_rate": round((s["shares"] or 0) / s["views"], 5) if s["views"] and s["shares"] is not None else None,
            "save_rate": round((s["saves"] or 0) / s["views"], 5) if s["views"] and s["saves"] is not None else None,
            "avg_view_pct": s["avg_view_pct"],
        }
        predicted = h.get("predicted_views_range")
        rows.append({
            "post_id": p["post_id"], "platform": p["platform"], "cell": cell, "views": s["views"],
            "beat_account_median": win, "audience_quality": quality,
            "prediction": predicted,
            "prediction_hit": (predicted[0] <= s["views"] <= predicted[1]) if predicted else None,
            "hypothesis": h.get("statement"),
            "evidence_strength": "A/B" if h.get("ab_group") else "observational (correlational only)",
        })
    return {"status": "ok", "account_median_views": med, "posts": rows,
            "cells": {k: {"wins": v.wins, "losses": v.losses, "posterior_mean": round(v.mean(), 3)} for k, v in cells.items()}}
