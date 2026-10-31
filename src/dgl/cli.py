"""`dgl` command line — one subcommand per pipeline stage, all safe to re-run."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ROOT, settings
from .store import Store, loads, new_id, now_iso


def _store(s) -> Store:
    return Store(s.data_dir / "lab.sqlite")


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_status(a, s):
    from .integrations import run_checks
    res = run_checks(s, _store(s))
    if a.json:
        _print(res)
    else:
        for k, v in res.items():
            print(f"{v['status']:<11} {k:<22} {v['detail']}")


def cmd_discover(a, s):
    from .discovery.engine import discover_youtube
    from .pipeline.jobs import run_job
    from .sources.youtube import QuotaBudget, YouTubeSource
    st = _store(s)
    out = {}
    if a.platform in ("youtube", "all"):
        yt = YouTubeSource(s.youtube_api_key, QuotaBudget(a.quota or s.youtube_daily_quota), s.region, s.language)
        out["youtube"] = run_job(st, s, "discover_youtube", lambda: discover_youtube(
            st, yt, n_queries=a.queries, chart_pages=a.chart_pages, days=a.days))
    if a.platform in ("instagram", "all"):
        from .sources.instagram_apify import InstagramApifySource
        ig = InstagramApifySource(s.apify_token, s.apify_reel_actor, s.apify_hashtag_actor, s.apify_max_usd)

        def run_ig():
            from .discovery.explore import QueryPlanner
            from .discovery.engine import corpus_scores
            planner = QueryPlanner()
            planner.update_corpus([(v, sc) for v, sc, _ in corpus_scores(st)])
            tags = [t.lstrip("#") for t, _ in planner.plan(a.queries, "ko") if t.startswith("#")] or ["일상"]
            items = ig.hashtag_reels(tags[: a.queries], limit=a.ig_limit)
            n = 0
            for it in items:
                rows = ig.to_rows(it, f"apify:hashtag:{','.join(tags)}")
                if rows:
                    st.upsert("videos", rows[0])
                    st.insert_snapshot_if_new(rows[1])
                    n += 1
            return {"hashtags": tags, "items": len(items), "kept": n, "cost": {"apify_max_usd": s.apify_max_usd}}
        out["instagram"] = run_job(st, s, "discover_instagram", run_ig)
    _print(out)


def cmd_snapshot(a, s):
    from .discovery.engine import snapshot_youtube
    from .pipeline.jobs import run_job
    from .sources.youtube import QuotaBudget, YouTubeSource
    st = _store(s)
    yt = YouTubeSource(s.youtube_api_key, QuotaBudget(a.quota or 500), s.region, s.language)
    _print(run_job(st, s, "snapshot_youtube", lambda: snapshot_youtube(st, yt)))


def cmd_analyze(a, s):
    from .analysis.media import analyze_file
    st = _store(s)
    ev = Path(a.evidence_dir) if a.evidence_dir else s.work_dir / "evidence" / (a.video_id or Path(a.file).stem)
    rec = analyze_file(Path(a.file), ev, a.rights, Path(a.srt) if a.srt else None)
    aid = new_id("an")
    st.upsert("analyses", {"analysis_id": aid, "platform": a.platform, "video_id": a.video_id or Path(a.file).stem,
                           "access_level": rec["access_level"], "media_source": str(a.file), "rights_basis": a.rights,
                           "created_at": now_iso(), "features": rec["features"],
                           "observations": {"observations": rec["observations"], "frames": rec["frames"],
                                            "transcript": rec["transcript"], "loudness": rec["loudness"],
                                            "cuts": rec["cuts"], "black_segments": rec["black_segments"]}})
    if a.out:
        Path(a.out).write_text(json.dumps({"analysis_id": aid, **rec}, ensure_ascii=False, indent=2), encoding="utf-8")
    _print({"analysis_id": aid, "access_level": rec["access_level"], "features": rec["features"]})


def cmd_cluster(a, s):
    from .clustering.run import run_clustering
    res = run_clustering(_store(s), min_size=a.min_size)
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _print({k: v for k, v in res.items() if k != "spaces"} | {
        sp: {"method": d["method"], "silhouette": d["silhouette"],
             "clusters": [(c["cluster_id"], c["label"], c["stats"]["size"]) for c in d["clusters"]]}
        for sp, d in res.get("spaces", {}).items()})


def cmd_rank(a, s):
    from .clustering.run import run_clustering
    from .ranking.opportunity import rank
    st = _store(s)
    res = json.loads(Path(a.clusters).read_text()) if a.clusters else run_clustering(st)
    if res.get("status") == "skipped":
        _print(res)
        return
    opps = rank(res["spaces"]["topic"]["clusters"], res["spaces"]["format"]["clusters"], res["n_videos"], n_slots=a.slots)
    for o in opps:
        o["opportunity_id"] = new_id("opp")
        st.upsert("opportunities", {"opportunity_id": o["opportunity_id"], "run_id": res["spaces"]["topic"]["run_id"],
                                    "created_at": now_iso(), "topic_cluster": o["topic_cluster"],
                                    "format_cluster": o["format_cluster"], "score": o["score"], "score_low": o["score_low"],
                                    "score_high": o["score_high"], "is_exploration": o["is_exploration"],
                                    "components": o["components"], "evidence": o["evidence"]})
    if a.out:
        Path(a.out).write_text(json.dumps(opps, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(opps)


def cmd_concepts(a, s):
    from .creative.concepts import validate
    st = _store(s)
    known = {f"{r['platform']}:{r['video_id']}" for r in st.query("SELECT platform, video_id FROM videos")}
    known |= {f"own:{r['post_id']}" for r in st.query("SELECT post_id FROM own_posts")}
    sec = json.loads((ROOT / "research" / "secondary_evidence.json").read_text())
    sec_ids = {e["id"] for e in sec["items"]}
    report = {}
    for f in sorted(Path(a.dir).glob("*.json")):
        c = json.loads(f.read_text())
        problems = validate(c, known, sec_ids)
        report[f.name] = problems or "OK"
        if not problems:
            st.upsert("concepts", {"concept_id": c["concept_id"], "opportunity_id": c["opportunity_id"],
                                   "created_at": now_iso(), "status": "proposed", "title_ko": c["title_ko"], "payload": c})
    _print(report)
    if any(v != "OK" for v in report.values()):
        sys.exit(1)


def cmd_render(a, s):
    from .qc.video_qc import run_qc
    from .render.build import build
    spec = json.loads(Path(a.spec).read_text())
    out_dir = Path(a.out_dir)
    built = build(spec, out_dir)
    qc = run_qc(Path(built["video"]), Path(built["srt"]), spec["duration"], s.work_dir / "qc" / spec["concept_id"])
    (out_dir / f"{Path(built['video']).stem}.qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    st = _store(s)
    pid = new_id("prod")
    st.upsert("productions", {"production_id": pid, "concept_id": spec["concept_id"], "created_at": now_iso(),
                              "status": "awaiting_approval" if qc["pass"] else "qc_failed", "video_path": built["video"],
                              "storage_url": None, "qc": qc, "approved_by": None, "approved_at": None})
    _print({"production_id": pid, **built, "qc_pass": qc["pass"],
            "failed": [c for c in qc["checks"] if not c["pass"]]})


def cmd_approve(a, s):
    """Human approval gate — the only path that marks a production publishable."""
    st = _store(s)
    rows = st.query("SELECT * FROM productions WHERE production_id=?", (a.production_id,))
    if not rows:
        sys.exit(f"unknown production {a.production_id}")
    p = rows[0]
    if not loads(p["qc"], {}).get("pass"):
        sys.exit("QC has not passed; refusing approval")
    storage = p["storage_url"]
    if s.r2_account_id and not storage:
        from .sinks import R2Sink
        storage = R2Sink(s).put(Path(p["video_path"]), f"productions/{a.production_id}.mp4")
    st.upsert("productions", {**p, "status": "approved", "approved_by": a.by, "approved_at": now_iso(),
                              "storage_url": storage, "qc": loads(p["qc"])})
    _print({"production_id": a.production_id, "status": "approved", "storage_url": storage,
            "note": "publishing is manual; register the live post with `dgl post-register`"})


def cmd_post_register(a, s):
    st = _store(s)
    pid = new_id("post")
    st.upsert("own_posts", {"post_id": pid, "production_id": a.production_id, "concept_id": a.concept_id,
                            "platform": a.platform, "platform_video_id": a.platform_video_id, "url": a.url,
                            "published_at": a.published_at or now_iso(), "hypothesis": json.loads(a.hypothesis)})
    _print({"post_id": pid})


def cmd_feedback(a, s):
    from .feedback.performance import review
    _print(review(_store(s)))


def cmd_pull(a, s):
    from .sinks import SupabaseSink
    _print({"pulled": SupabaseSink(s).pull(_store(s))})


def cmd_sync(a, s):
    from .sinks import SupabaseSink
    _print({"synced_rows": SupabaseSink(s).drain(_store(s))})


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="dgl", description="DINARY Growth Lab content engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("status"); x.add_argument("--json", action="store_true"); x.set_defaults(fn=cmd_status)
    x = sub.add_parser("discover"); x.add_argument("--platform", default="youtube", choices=["youtube", "instagram", "all"])
    x.add_argument("--queries", type=int, default=6); x.add_argument("--chart-pages", type=int, default=4)
    x.add_argument("--days", type=int, default=14); x.add_argument("--quota", type=int)
    x.add_argument("--ig-limit", type=int, default=30); x.set_defaults(fn=cmd_discover)
    x = sub.add_parser("snapshot"); x.add_argument("--quota", type=int); x.set_defaults(fn=cmd_snapshot)
    x = sub.add_parser("analyze"); x.add_argument("file"); x.add_argument("--rights", required=True)
    x.add_argument("--platform", default="own"); x.add_argument("--video-id"); x.add_argument("--srt")
    x.add_argument("--out"); x.add_argument("--evidence-dir", help="where frames are kept (default: work/evidence)")
    x.set_defaults(fn=cmd_analyze)
    x = sub.add_parser("cluster"); x.add_argument("--min-size", type=int, default=3); x.add_argument("--out")
    x.set_defaults(fn=cmd_cluster)
    x = sub.add_parser("rank"); x.add_argument("--clusters"); x.add_argument("--slots", type=int, default=6)
    x.add_argument("--out"); x.set_defaults(fn=cmd_rank)
    x = sub.add_parser("concepts"); x.add_argument("--dir", default=str(ROOT / "outputs" / "concepts"))
    x.set_defaults(fn=cmd_concepts)
    x = sub.add_parser("render"); x.add_argument("spec"); x.add_argument("--out-dir", default=str(ROOT / "outputs" / "videos"))
    x.set_defaults(fn=cmd_render)
    x = sub.add_parser("approve"); x.add_argument("production_id"); x.add_argument("--by", required=True)
    x.set_defaults(fn=cmd_approve)
    x = sub.add_parser("post-register"); x.add_argument("--production-id", required=True)
    x.add_argument("--concept-id", required=True); x.add_argument("--platform", required=True)
    x.add_argument("--platform-video-id", required=True); x.add_argument("--url", required=True)
    x.add_argument("--published-at"); x.add_argument("--hypothesis", required=True, help="JSON")
    x.set_defaults(fn=cmd_post_register)
    x = sub.add_parser("feedback"); x.set_defaults(fn=cmd_feedback)
    x = sub.add_parser("sync"); x.set_defaults(fn=cmd_sync)
    x = sub.add_parser("pull", help="restore working set from Supabase"); x.set_defaults(fn=cmd_pull)
    a = p.parse_args(argv)
    a.fn(a, settings())


if __name__ == "__main__":
    main()
