"""Emergent pattern discovery with no predefined taxonomy.

Two separate spaces are clustered independently:

* **topic space**  — *what the video is about*: character n-gram TF-IDF over
  title, description, hashtags, tags and transcript (works for Korean without a
  tokenizer), reduced with truncated SVD. A pluggable `embed_fn` can replace
  this with a neural embedding model when one is reachable.
* **format space** — *how the video attracts attention*: measured structural
  features (cut rate, time-to-first-text, speech ratio, loopability, …) from the
  media analysis, plus metadata-level form features for metadata-only rows.

Clusters are found by density (HDBSCAN: no k, allows noise) with a
silhouette-selected KMeans fallback for small samples. Labels are produced
*after* clustering from distinguishing terms / feature deviations, and every
cluster keeps its member list so each pattern is traceable to source videos.
`lineage()` links clusters across runs (emerged / persisted / merged / split /
dissolved) so the taxonomy can change over time.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable

import numpy as np
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize

WORD_RE = re.compile(r"#?[가-힣]{2,}|#?[a-zA-Z]{3,}", re.UNICODE)
LABEL_STOP = {"shorts", "#shorts", "short", "the", "and", "you", "this", "that", "with", "for", "구독", "좋아요",
              "쇼츠", "#쇼츠", "릴스", "#reels", "#fyp", "#viral", "http", "https", "www", "com"}

FORMAT_FEATURES = [
    "duration_s", "cuts_per_10s", "hard_cuts_per_10s", "mean_shot_s", "first_cut_s", "text_on_screen_ratio", "first_text_s",
    "first_speech_s", "speech_ratio", "words_per_s", "silence_ratio", "mean_brightness", "mean_saturation",
    "loop_similarity", "title_len", "title_has_question", "title_has_number", "n_hashtags",
]


def video_text(v: dict) -> str:
    parts = [v.get("title") or "", (v.get("description") or "")[:400],
             " ".join("#" + h for h in v.get("hashtags") or []), " ".join(v.get("tags") or []),
             v.get("transcript_text") or ""]
    text = " ".join(parts)
    text = re.sub(r"https?://\S+", " ", text)
    return text


@dataclass
class Clustering:
    space: str
    labels: np.ndarray
    X: np.ndarray
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    quality: float | None = None


def topic_embeddings(videos: list[dict], embed_fn: Callable[[list[str]], np.ndarray] | None = None) -> np.ndarray:
    texts = [video_text(v) for v in videos]
    if embed_fn is not None:
        return normalize(np.asarray(embed_fn(texts), dtype=float))
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True, max_features=20000)
    try:
        M = vec.fit_transform(texts)
    except ValueError:  # vocabulary empty (too few docs)
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
        M = vec.fit_transform(texts)
    k = max(2, min(64, M.shape[0] - 1, M.shape[1] - 1))
    Z = TruncatedSVD(n_components=k, random_state=0).fit_transform(M)
    return normalize(Z)


def format_matrix(feature_rows: list[dict]) -> tuple[np.ndarray, list[str]]:
    cols = [c for c in FORMAT_FEATURES if sum(r.get(c) is not None for r in feature_rows) >= max(2, len(feature_rows) // 3)]
    if not cols:
        return np.zeros((len(feature_rows), 0)), []
    raw = np.array([[np.nan if r.get(c) is None else float(r[c]) for c in cols] for r in feature_rows])
    med = np.nanmedian(raw, axis=0)
    miss = np.isnan(raw)
    raw[miss] = np.take(med, np.where(miss)[1])
    sd = raw.std(axis=0)
    sd[sd == 0] = 1
    Z = (raw - raw.mean(axis=0)) / sd
    # missingness is itself informative (e.g. no speech) — append indicators
    ind_cols = [i for i in range(len(cols)) if miss[:, i].any() and not miss[:, i].all()]
    if ind_cols:
        Z = np.hstack([Z, miss[:, ind_cols].astype(float)])
        cols = cols + [f"missing:{cols[i]}" for i in ind_cols]
    return Z, cols


def find_clusters(X: np.ndarray, space: str, min_size: int = 3, seed: int = 0) -> Clustering:
    n = X.shape[0]
    if n < 2 * min_size or X.shape[1] == 0:
        return Clustering(space, np.zeros(n, dtype=int), X, "single", {"reason": "too few samples"})
    best: Clustering | None = None
    try:
        h = HDBSCAN(min_cluster_size=min_size, min_samples=max(1, min_size - 1), copy=True).fit(X)
        labs = h.labels_
        k = len(set(labs) - {-1})
        mask = labs >= 0
        if k >= 2 and mask.sum() > k:
            q = float(silhouette_score(X[mask], labs[mask]))
            best = Clustering(space, labs, X, "hdbscan", {"min_cluster_size": min_size}, q)
    except ValueError:
        pass
    # KMeans fallback / competitor: choose k by silhouette, k never fixed in advance
    for k in range(2, max(3, min(15, n // min_size) + 1)):
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
        sizes = np.bincount(km.labels_)
        if sizes.min() < 2:
            continue
        q = float(silhouette_score(X, km.labels_))
        # require a clear margin before preferring KMeans over the density solution
        if best is None or (best.method != "hdbscan" and q > (best.quality or -1)) or (
            best.method == "hdbscan" and q > (best.quality or -1) + 0.1
        ):
            best = Clustering(space, km.labels_.copy(), X, "kmeans+silhouette", {"k": k}, q)
    if best is None:
        return Clustering(space, np.zeros(n, dtype=int), X, "single", {"reason": "no separable structure"})
    # mark weakly-assigned KMeans points as noise so outliers do not distort labels
    if best.method.startswith("kmeans"):
        from sklearn.metrics import silhouette_samples
        s = silhouette_samples(X, best.labels)
        best.labels = np.where(s < 0, -1, best.labels)
    return best


def _words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text) if w.lower() not in LABEL_STOP]


def describe_topic_clusters(videos: list[dict], labels: np.ndarray, top: int = 6) -> dict[int, dict]:
    """class-based TF-IDF: terms frequent inside a cluster but rare across clusters."""
    docs: dict[int, Counter] = {}
    for v, l in zip(videos, labels):
        if l < 0:
            continue
        docs.setdefault(int(l), Counter()).update(set(_words(video_text(v))))
    n_clusters = len(docs)
    df = Counter(w for c in docs.values() for w in c)
    out = {}
    for l, c in docs.items():
        size = int((labels == l).sum())
        scores = {w: (cnt / size) * math.log(1 + n_clusters / df[w]) for w, cnt in c.items() if cnt >= 2 or size <= 3}
        terms = sorted(scores, key=lambda w: -scores[w])[:top]
        out[l] = {"top_terms": terms, "label": " · ".join(terms[:3]) or f"topic-{l}",
                  "coverage": {w: round(c[w] / size, 2) for w in terms}}
    return out


def describe_format_clusters(Z: np.ndarray, cols: list[str], labels: np.ndarray, top: int = 4) -> dict[int, dict]:
    out = {}
    for l in sorted(set(labels.tolist()) - {-1}):
        centroid = Z[labels == l].mean(axis=0)
        order = np.argsort(-np.abs(centroid))[:top]
        traits = [{"feature": cols[i], "z": round(float(centroid[i]), 2)} for i in order if abs(centroid[i]) >= 0.4]
        label = ", ".join(f"{'high' if t['z'] > 0 else 'low'} {t['feature']}" for t in traits[:3]) or "average form"
        out[int(l)] = {"traits": traits, "label": label}
    return out


def cluster_stats(members: list[dict], recent_days: int = 7) -> dict[str, Any]:
    """Momentum and saturation from member metrics. Each member has keys from discovery.metrics + video."""
    from ..discovery.metrics import parse_ts
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    pops = [m["popularity"] for m in members if m.get("popularity") is not None]
    vel = [m["velocity_vph"] for m in members if m.get("velocity_vph") is not None]
    rel = [m["log_outperformance"] for m in members if m.get("log_outperformance") is not None]
    creators = Counter(m.get("creator_id") for m in members)
    pubs = [parse_ts(m.get("published_at")) for m in members]
    recent = sum(1 for p in pubs if p and now - p <= timedelta(days=recent_days))
    prior = sum(1 for p in pubs if p and timedelta(days=recent_days) < now - p <= timedelta(days=2 * recent_days))
    hhi = sum((c / len(members)) ** 2 for c in creators.values()) if members else 1.0
    platforms = Counter(m.get("platform") for m in members)
    return {
        "size": len(members),
        "n_creators": len(creators),
        "creator_hhi": round(hhi, 3),  # 1.0 = single creator; low = repeated across independent creators
        "platforms": dict(platforms),
        "median_popularity": round(median(pops), 3) if pops else None,
        "median_velocity_vph": round(median(vel), 1) if vel else None,
        "n_with_velocity": len(vel),
        "median_log_outperformance": round(median(rel), 3) if rel else None,
        "n_with_baseline": len(rel),
        "recent_count": recent,
        "prior_count": prior,
        # publication momentum: are more members appearing recently than in the prior window?
        "publication_momentum": round((recent - prior) / max(1, recent + prior), 3),
    }


def lineage(prev: dict[str, set[str]], cur: dict[str, set[str]], threshold: float = 0.25) -> dict[str, Any]:
    """Link clusters between two runs by member overlap (overlap coefficient)."""
    def ov(a: set, b: set) -> float:
        return len(a & b) / min(len(a), len(b)) if a and b else 0.0

    links = [(p, c, ov(pm, cm)) for p, pm in prev.items() for c, cm in cur.items()]
    links = [(p, c, s) for p, c, s in links if s >= threshold]
    to_prev: dict[str, list[str]] = {c: [] for c in cur}
    to_cur: dict[str, list[str]] = {p: [] for p in prev}
    for p, c, _ in links:
        to_prev[c].append(p)
        to_cur[p].append(c)
    events: dict[str, dict] = {}
    for c, ps in to_prev.items():
        if not ps:
            events[c] = {"event": "emerged", "from": []}
        elif len(ps) > 1:
            events[c] = {"event": "merged", "from": ps}
        elif len(to_cur[ps[0]]) > 1:
            events[c] = {"event": "split", "from": ps}
        else:
            events[c] = {"event": "persisted", "from": ps}
    dissolved = [p for p, cs in to_cur.items() if not cs]
    return {"current": events, "dissolved": dissolved,
            "links": [{"prev": p, "cur": c, "overlap": round(s, 3)} for p, c, s in links]}
