"""Popularity metrics: absolute, creator-relative, and measured velocity.

Velocity is only reported when at least two snapshots exist; a single view
count says nothing about growth, so `velocity_vph` is None in that case
rather than views / age (which would confuse accumulated reach with momentum).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_between(a: str, b: str) -> float | None:
    ta, tb = parse_ts(a), parse_ts(b)
    if not ta or not tb:
        return None
    return (tb - ta).total_seconds() / 3600


def video_metrics(video: dict, snaps: list[dict], creator: dict | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    snaps = sorted(snaps, key=lambda s: s["observed_at"])
    last = snaps[-1] if snaps else {}
    views = last.get("views")
    pub = parse_ts(video.get("published_at"))
    age_h = (now - pub).total_seconds() / 3600 if pub else None

    out: dict[str, Any] = {
        "views": views,
        "likes": last.get("likes"),
        "comments": last.get("comments"),
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "n_snapshots": len(snaps),
        "like_rate": (last["likes"] / views) if views and last.get("likes") is not None else None,
        "comment_rate": (last["comments"] / views) if views and last.get("comments") is not None else None,
        "lifetime_vph": (views / age_h) if views is not None and age_h and age_h > 0 else None,
        "velocity_vph": None,
        "acceleration": None,
        "creator_relative": None,
        "log_outperformance": None,
    }
    # measured velocity between the two most recent snapshots
    if len(snaps) >= 2 and snaps[-1].get("views") is not None and snaps[-2].get("views") is not None:
        dh = hours_between(snaps[-2]["observed_at"], snaps[-1]["observed_at"])
        if dh and dh > 0:
            out["velocity_vph"] = (snaps[-1]["views"] - snaps[-2]["views"]) / dh
    if len(snaps) >= 3 and all(s.get("views") is not None for s in snaps[-3:]):
        d1 = hours_between(snaps[-3]["observed_at"], snaps[-2]["observed_at"])
        d2 = hours_between(snaps[-2]["observed_at"], snaps[-1]["observed_at"])
        if d1 and d2 and d1 > 0 and d2 > 0:
            v1 = (snaps[-2]["views"] - snaps[-3]["views"]) / d1
            v2 = (snaps[-1]["views"] - snaps[-2]["views"]) / d2
            out["acceleration"] = (v2 - v1) / ((d1 + d2) / 2)
    base = (creator or {}).get("baseline_median_views")
    if views is not None and base and (creator or {}).get("baseline_n", 0) >= 5:
        out["creator_relative"] = views / base
        out["log_outperformance"] = math.log10((views + 1) / (base + 1))
    return out


def popularity_score(m: dict[str, Any]) -> float:
    """Blend of absolute reach and creator-relative outperformance (log scale).

    Creator-relative outperformance is weighted higher because it controls for
    audience size; absolute views keep tiny-channel flukes from dominating.
    """
    absolute = math.log10((m.get("views") or 0) + 1)
    rel = m.get("log_outperformance")
    if rel is None:
        return absolute / 7  # ~1.0 at 10M views
    return 0.35 * absolute / 7 + 0.65 * (0.5 + max(min(rel, 2), -2) / 4)
