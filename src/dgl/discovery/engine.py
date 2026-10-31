"""Discovery run: chart sampling + adaptive search + hydration + baselines + coverage report."""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from ..http import HttpError
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
    baseline_top_creators: int = 60,
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
    kept, new, creators = 0, 0, Counter()
    try:
        for item in yt.videos(list(found)):
            video, snap = yt.to_rows(item, found[item["id"]])
            if video["duration_s"] is None or video["duration_s"] > SHORTS_MAX_S:
                continue
            new += store.upsert_video(video)
            store.insert_snapshot_if_new(snap)
            creators[video["creator_id"]] += 1
            kept += 1
    except QuotaExceeded as e:
        report["errors"].append(str(e))

    # creator baselines (recent Shorts) for creator-relative outperformance
    report["baselines"] = refresh_baselines(store, yt, list(creators), max_creators=baseline_top_creators)
    report["errors"] += report["baselines"].pop("errors")

    # record yields so the planner learns which arms surface outperformers
    scores = {v["video_id"]: s for v, s, _ in corpus_scores(store, "youtube")}
    for q in report["queries"]:
        via = f"search:{q['reason']}:{q['term']}"
        planner.record_yield(q["term"], [scores[i] for i, v in found.items() if v == via and i in scores])

    report.update(
        finished_at=now_iso(),
        candidates_found=len(found),
        short_form_kept=kept,
        new_videos=new,
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


def refresh_baselines(store: Store, yt: YouTubeSource, creator_ids: list[str] | None = None,
                      max_creators: int = 60, max_age_days: int = 7) -> dict[str, Any]:
    """Fetch recent-Shorts baselines, highest-reach creators first; skip fresh ones.

    A channel whose uploads cannot be listed (404 / hidden) gets a row without a
    baseline instead of failing the run; the reason is kept in `raw`.
    """
    if creator_ids is None:
        creator_ids = [r["creator_id"] for r in store.query("SELECT DISTINCT creator_id FROM videos WHERE platform='youtube'")]
    top_views = {r["creator_id"]: r["v"] or 0 for r in store.query(
        "SELECT v.creator_id, MAX(s.views) v FROM videos v JOIN video_snapshots s USING (platform, video_id) "
        "WHERE v.platform='youtube' GROUP BY v.creator_id")}
    fresh = {r["creator_id"] for r in store.query(
        "SELECT creator_id FROM creators WHERE platform='youtube' AND baseline_n IS NOT NULL "
        "AND observed_at >= datetime('now', ?)", (f"-{max_age_days} days",))}
    todo = sorted((c for c in set(creator_ids) if c and c not in fresh), key=lambda c: -top_views.get(c, 0))
    out: dict[str, Any] = {"requested": len(todo), "with_baseline": 0, "unavailable": 0, "errors": []}
    try:
        items = {ch["id"]: ch for ch in yt.channels(todo)}
        for i, cid in enumerate(todo):
            ch = items.get(cid)
            if ch is None:
                continue
            views, note = None, None
            if i < max_creators:
                uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                try:
                    views = yt.recent_upload_views(uploads) if uploads else []
                except HttpError as e:
                    views, note = [], f"uploads not listable: HTTP {e.status}"
                    out["unavailable"] += 1
            row = yt.channel_row(ch, views)
            if note:
                row["raw"] = {**row["raw"], "baseline_note": note}
            store.upsert("creators", row)
            out["with_baseline"] += int(bool(views) and len(views) >= 5)
    except QuotaExceeded as e:
        out["errors"].append(str(e))
    return out


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
