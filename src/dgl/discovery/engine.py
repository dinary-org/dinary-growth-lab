"""Discovery run: chart sampling + adaptive search + hydration + baselines + coverage report."""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from ..store import Store, loads, now_iso
from ..sources.youtube import SHORTS_MAX_S, QuotaExceeded, YouTubeSource
from .explore import QueryPlanner
from .metrics import popularity_score, video_metrics

log = logging.getLogger("dgl.discovery")


def corpus_scores(store: Store, platform: str | None = None) -> list[tuple[dict, float, dict]]:
    vids = store.query("SELECT * FROM videos" + (" WHERE platform=?" if platform else ""), (platform,) if platform else ())
    out = []
    for v in vids:
        v["hashtags"] = loads(v["hashtags"], [])
        snaps = store.query(
            "SELECT * FROM video_snapshots WHERE platform=? AND video_id=?", (v["platform"], v["video_id"])
        )
        creator = store.query(
            "SELECT * FROM creators WHERE platform=? AND creator_id=?", (v["platform"], v["creator_id"])
        )
        m = video_metrics(v, snaps, creator[0] if creator else None)
        out.append((v, popularity_score(m), m))
    return out


def discover_youtube(
    store: Store,
    yt: YouTubeSource,
    n_queries: int = 6,
    chart_pages: int = 4,
    days: int = 14,
    baseline_top_creators: int = 25,
    seed: int | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {"platform": "youtube", "started_at": now_iso(), "queries": [], "errors": []}
    found: dict[str, str] = {}

    def add(ids: list[str], via: str) -> None:
        for i in ids:
            found.setdefault(i, via)

    try:
        add(yt.most_popular_ids(pages=chart_pages), f"chart:mostPopular:{yt.region}")
    except QuotaExceeded as e:
        report["errors"].append(str(e))

    planner = QueryPlanner(seed=seed)
    planner.update_corpus([(v, s) for v, s, _ in corpus_scores(store, "youtube")])
    for term, reason in planner.plan(n_queries, yt.language):
        try:
            ids = yt.search_short_ids(term, order="viewCount", days=days)
            report["queries"].append({"term": term, "reason": reason, "results": len(ids)})
            add(ids, f"search:{reason}:{term}")
        except QuotaExceeded as e:
            report["errors"].append(str(e))
            break

    # hydrate metadata + statistics; keep only short-form by duration
    kept, creators = 0, Counter()
    try:
        for item in yt.videos(list(found)):
            video, snap = yt.to_rows(item, found[item["id"]])
            if video["duration_s"] is None or video["duration_s"] > SHORTS_MAX_S:
                continue
            store.upsert("videos", video)
            store.insert_snapshot_if_new(snap)
            creators[video["creator_id"]] += 1
            kept += 1
    except QuotaExceeded as e:
        report["errors"].append(str(e))

    # creator baselines (recent uploads) for creator-relative outperformance
    try:
        ch_items = list(yt.channels(list(creators)))
        for ch in ch_items[:baseline_top_creators]:
            uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            views = yt.recent_upload_views(uploads) if uploads else []
            store.upsert("creators", yt.channel_row(ch, views))
        for ch in ch_items[baseline_top_creators:]:
            store.upsert("creators", yt.channel_row(ch, None))
    except QuotaExceeded as e:
        report["errors"].append(str(e))

    # record yields so the planner learns which arms surface outperformers
    scores = {v["video_id"]: s for v, s, _ in corpus_scores(store, "youtube")}
    for q in report["queries"]:
        via = f"search:{q['reason']}:{q['term']}"
        planner.record_yield(q["term"], [scores[i] for i, v in found.items() if v == via and i in scores])

    report.update(
        finished_at=now_iso(),
        candidates_found=len(found),
        short_form_kept=kept,
        distinct_creators=len(creators),
        quota_used=yt.budget.used,
        sampling_notes=[
            "mostPopular chart is region-scoped and favours already-viral items (survivorship bias)",
            "search.list order=viewCount favours older items within the window; recency handled by publishedAfter",
            "Shorts inferred from duration <= 180 s; long videos under 3 min are included",
            "shares/saves are not exposed by the Data API",
        ],
    )
    return report


def snapshot_youtube(store: Store, yt: YouTubeSource, max_age_days: int = 30) -> dict[str, Any]:
    """Re-observe tracked videos so velocity is measured, not inferred."""
    ids = [r["video_id"] for r in store.query(
        "SELECT video_id FROM videos WHERE platform='youtube' AND published_at >= datetime('now', ?)",
        (f"-{max_age_days} days",),
    )]
    n_new = 0
    for item in yt.videos(ids):
        _, snap = yt.to_rows(item, "snapshot")
        n_new += store.insert_snapshot_if_new(snap)
    return {"tracked": len(ids), "new_snapshots": n_new, "quota_used": yt.budget.used, "at": now_iso()}
