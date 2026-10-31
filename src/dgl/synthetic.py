"""Synthetic corpora for testing the discovery mechanics — NEVER research data.

Vocabularies are random Hangul syllable strings with no meaning, so a test
corpus cannot smuggle in a content taxonomy or masquerade as a trend finding.
Each generated video carries its hidden ground-truth labels under `_truth`
so tests can score cluster recovery (ARI) and lineage events.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

SYLLABLES = [chr(c) for c in range(0xAC00, 0xD7A4, 37)]  # spread across the Hangul block


def make_vocab(rng: random.Random, n: int = 12) -> list[str]:
    return ["".join(rng.choice(SYLLABLES) for _ in range(rng.choice((2, 3)))) for _ in range(n)]


FORMAT_PROFILES = {  # latent "how" profiles; feature means only, names are opaque
    "F1": {"cuts_per_10s": 6.0, "speech_ratio": 0.2, "text_on_screen_ratio": 0.9, "first_text_s": 0.0, "loop_similarity": 0.4},
    "F2": {"cuts_per_10s": 1.0, "speech_ratio": 0.9, "text_on_screen_ratio": 0.3, "first_text_s": 2.5, "loop_similarity": 0.3},
    "F3": {"cuts_per_10s": 3.0, "speech_ratio": 0.5, "text_on_screen_ratio": 0.6, "first_text_s": 0.5, "loop_similarity": 0.95},
}


def synth_video(rng: random.Random, vid: str, topic: str, vocab: list[str], fmt: str, published: datetime,
                creator: str) -> tuple[dict, dict, dict]:
    words = rng.sample(vocab, 5) + [rng.choice(make_vocab(rng, 3))]  # topic words + noise
    title = " ".join(words[:4])
    video = {"platform": "synthetic", "video_id": vid, "url": f"synthetic://{vid}", "creator_id": creator,
             "creator_name": creator, "published_at": published.isoformat(), "title": title,
             "description": " ".join(words), "hashtags": [w for w in words[:2]], "tags": [],
             "duration_s": round(rng.uniform(12, 45), 1), "language": "ko", "discovered_via": "synthetic",
             "first_seen_at": published.isoformat(), "raw": {"_truth": {"topic": topic, "format": fmt}}}
    prof = FORMAT_PROFILES[fmt]
    feats = {k: max(0.0, rng.gauss(v, 0.12 * (abs(v) + 0.5))) for k, v in prof.items()}
    snap = {"platform": "synthetic", "video_id": vid, "observed_at": (published + timedelta(hours=24)).isoformat(),
            "views": int(10 ** rng.uniform(3, 6)), "likes": None, "comments": None, "shares": None, "saves": None,
            "source": "synthetic", "missing": ["likes", "comments", "shares", "saves"]}
    return video, snap, feats


def corpus(seed: int, topics: dict[str, int], start: datetime, span_days: float, fmt_for_topic: dict[str, str] | None = None,
           vocabs: dict[str, list[str]] | None = None, prefix: str = "v") -> tuple[list[tuple[dict, dict, dict]], dict[str, list[str]]]:
    rng = random.Random(seed)
    vocabs = dict(vocabs or {})
    rows = []
    i = 0
    for topic, n in topics.items():
        vocabs.setdefault(topic, make_vocab(rng))
        for _ in range(n):
            fmt = (fmt_for_topic or {}).get(topic) or rng.choice(list(FORMAT_PROFILES))
            pub = start + timedelta(days=rng.uniform(0, span_days))
            rows.append(synth_video(rng, f"{prefix}{i:03d}", topic, vocabs[topic], fmt, pub, f"cr{rng.randint(0, 40)}"))
            i += 1
    return rows, vocabs


def load(store, rows, analysis_level: str = "visual") -> None:
    from .store import new_id
    for video, snap, feats in rows:
        store.upsert("videos", video, replicate=False)
        store.upsert("video_snapshots", snap, replicate=False)
        store.upsert("analyses", {"analysis_id": new_id("an"), "platform": video["platform"], "video_id": video["video_id"],
                                  "access_level": analysis_level, "media_source": "synthetic", "rights_basis": "synthetic",
                                  "created_at": video["published_at"], "features": feats, "observations": []},
                     replicate=False)


NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
