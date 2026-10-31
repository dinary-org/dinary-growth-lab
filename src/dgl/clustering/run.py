"""Cluster-update job: builds both spaces from the store, persists clusters with lineage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from ..discovery.engine import corpus_scores
from ..discovery.metrics import parse_ts
from ..store import Store, loads, new_id, now_iso
from . import emergent as E


def _latest_features(store: Store) -> dict[tuple[str, str], dict]:
    rows = store.query("SELECT platform, video_id, access_level, features, observations, created_at FROM analyses "
                       "ORDER BY created_at")
    return {(r["platform"], r["video_id"]): r for r in rows}  # last analysis wins


def _previous_members(store: Store, space: str) -> tuple[str | None, dict[str, set[str]], dict[str, dict]]:
    runs = store.query("SELECT run_id FROM cluster_runs WHERE space=? ORDER BY created_at DESC LIMIT 1", (space,))
    if not runs:
        return None, {}, {}
    rid = runs[0]["run_id"]
    members: dict[str, set[str]] = {}
    for r in store.query("SELECT cluster_id, platform, video_id FROM cluster_members WHERE run_id=?", (rid,)):
        members.setdefault(r["cluster_id"], set()).add(f"{r['platform']}:{r['video_id']}")
    stats = {r["cluster_id"]: loads(r["stats"], {}) for r in store.query("SELECT * FROM clusters WHERE run_id=?", (rid,))}
    return rid, members, stats


def run_clustering(store: Store, min_size: int = 3, embed_fn=None, platform: str | None = None,
                   lookback_days: float | None = 30, now: datetime | None = None) -> dict[str, Any]:
    """Cluster videos published within the lookback window; older videos age out so patterns can dissolve."""
    now = now or datetime.now(timezone.utc)
    corpus = corpus_scores(store, platform)
    if lookback_days is not None:
        cutoff = now - timedelta(days=lookback_days)
        corpus = [c for c in corpus if (p := parse_ts(c[0].get("published_at"))) is None or p >= cutoff]
    if len(corpus) < 2 * min_size:
        return {"status": "skipped", "reason": f"need >= {2 * min_size} videos, have {len(corpus)}"}
    feats = _latest_features(store)
    videos, member_info, feature_rows = [], [], []
    for v, pop, m in corpus:
        v["tags"] = loads(v.get("tags"), [])
        a = feats.get((v["platform"], v["video_id"]))
        f = loads(a["features"], {}) if a else {}
        title = v.get("title") or ""
        f.setdefault("duration_s", v.get("duration_s"))
        f.setdefault("title_len", len(title))
        f.setdefault("title_has_question", int("?" in title))
        f.setdefault("title_has_number", int(any(ch.isdigit() for ch in title)))
        f.setdefault("n_hashtags", len(v.get("hashtags") or []))
        videos.append(v)
        feature_rows.append(f)
        member_info.append({**m, "popularity": pop, "platform": v["platform"], "video_id": v["video_id"],
                            "creator_id": v.get("creator_id"), "published_at": v.get("published_at"),
                            "access_level": a["access_level"] if a else "metadata_only",
                            "discovered_via": v.get("discovered_via")})

    results: dict[str, Any] = {"created_at": now_iso(), "n_videos": len(videos), "spaces": {}}
    Xt = E.topic_embeddings(videos, embed_fn)
    Zf, fcols = E.format_matrix(feature_rows)
    for space, X in (("topic", Xt), ("format", Zf)):
        cl = E.find_clusters(X, space, min_size=min_size)
        desc = (E.describe_topic_clusters(videos, cl.labels) if space == "topic"
                else E.describe_format_clusters(Zf, fcols, cl.labels))
        run_id = new_id(f"cr_{space}")
        prev_id, prev_members, prev_stats = _previous_members(store, space)

        cur_members: dict[str, set[str]] = {}
        clusters = []
        for l in sorted(set(cl.labels.tolist())):
            idx = np.where(cl.labels == l)[0]
            cid = f"{space[0]}{l}" if l >= 0 else f"{space[0]}_noise"
            members = [member_info[i] for i in idx]
            cur_members[cid] = {f"{m['platform']}:{m['video_id']}" for m in members}
            clusters.append((cid, l, members))
        lin = E.lineage({k: v for k, v in prev_members.items() if not k.endswith("_noise")},
                        {k: v for k, v in cur_members.items() if not k.endswith("_noise")})

        store.upsert("cluster_runs", {"run_id": run_id, "created_at": now_iso(), "space": space,
                                      "params": {"method": cl.method, **cl.params, "quality_silhouette": cl.quality,
                                                 "features": fcols if space == "format" else "char_wb 2-4 tfidf+svd",
                                                 "previous_run": prev_id},
                                      "summary": {"n_clusters": len([c for c in clusters if c[1] >= 0]),
                                                  "noise": int((cl.labels < 0).sum()), "lineage": lin}})
        out_clusters = []
        for cid, l, members in clusters:
            st = E.cluster_stats(members)
            d = desc.get(l, {"label": "unclustered / noise"}) if l >= 0 else {"label": "unclustered / noise"}
            ev = lin["current"].get(cid, {"event": "noise", "from": []})
            # momentum delta vs predecessor(s): declining vs rising patterns
            prev_pop = [prev_stats[p].get("median_popularity") for p in ev["from"] if prev_stats.get(p)]
            prev_pop = [p for p in prev_pop if p is not None]
            delta = (st["median_popularity"] - sum(prev_pop) / len(prev_pop)) if prev_pop and st["median_popularity"] is not None else None
            st["popularity_delta_vs_prev"] = round(delta, 3) if delta is not None else None
            momentum = st["publication_momentum"] + (delta or 0)
            store.upsert("clusters", {"run_id": run_id, "cluster_id": cid, "space": space, "label": d["label"],
                                      "explanation": d, "size": st["size"], "n_creators": st["n_creators"],
                                      "momentum": round(momentum, 3), "lineage": ev, "stats": st})
            store.upsert_many("cluster_members", [
                {"run_id": run_id, "cluster_id": cid, "platform": m["platform"], "video_id": m["video_id"],
                 "weight": round(m["popularity"], 4)} for m in members])
            out_clusters.append({"cluster_id": cid, "label": d["label"], "description": d, "stats": st,
                                 "lineage": ev, "members": sorted(members, key=lambda m: -m["popularity"])})
        results["spaces"][space] = {"run_id": run_id, "method": cl.method, "params": cl.params,
                                    "silhouette": cl.quality, "lineage": lin, "clusters": out_clusters}
    return results
