"""Evidence-based opportunity ranking with explicit uncertainty and exploration.

An opportunity is a (topic cluster × format cluster) cell, or a topic cluster
alone when the format space is uninformative. Each component is computed
from member evidence and reported separately so the score is auditable:

  outperformance  median creator-relative log-outperformance (falls back to popularity)
  momentum        publication momentum + popularity change vs predecessor cluster
  independence    repeated success across independent creators (1 − HHI, n_creators)
  novelty         emerged/split clusters and recently published members
  saturation      penalty: many recent items from many creators competing
  shareability    PROXY only — comment/like rates (true shares are not public)
  feasibility     heuristic from measured form (cut rate, speech needs)
  own_history     Beta posterior from our own published results in that cluster

Scores are uncertain: we bootstrap members to obtain a 10–90% interval and
use Thompson draws to fill a reserved exploration share, so the system does
not keep producing only the last thing that worked.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import median
from typing import Any

WEIGHTS = {
    "outperformance": 0.28, "momentum": 0.16, "independence": 0.14, "novelty": 0.10,
    "saturation": -0.10, "shareability": 0.08, "feasibility": 0.10, "own_history": 0.14,
}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class OwnHistory:
    """Successes = our posts in this cluster that beat our account median; Beta(1,1) prior."""
    wins: int = 0
    losses: int = 0

    def mean(self) -> float:
        return (1 + self.wins) / (2 + self.wins + self.losses)

    def sample(self, rng: random.Random) -> float:
        return rng.betavariate(1 + self.wins, 1 + self.losses)


def components(members: list[dict], cluster: dict, corpus_size: int, own: OwnHistory) -> dict[str, float]:
    organic = [m for m in members if not m.get("ad_suspect")] or members
    rel = [m["log_outperformance"] for m in organic if m.get("log_outperformance") is not None]
    pop = [m["popularity"] for m in organic if m.get("popularity") is not None]
    # 0.5 = at the creator's baseline, 1.0 = 100x it (a /2 scale saturated at 10x on live data)
    outp = _clip01(0.5 + median(rel) / 4) if rel else (_clip01(median(pop)) if pop else 0.0)
    st = cluster.get("stats", {})
    mom = _clip01(0.5 + 0.5 * (st.get("publication_momentum") or 0) + (st.get("popularity_delta_vs_prev") or 0))
    creators = {m.get("creator_id") for m in members}
    hhi = sum((sum(1 for m in members if m.get("creator_id") == c) / len(members)) ** 2 for c in creators)
    indep = _clip01((1 - hhi) * min(1.0, len(creators) / 5))
    event = (cluster.get("lineage") or {}).get("event")
    novelty = _clip01((0.6 if event in ("emerged", "split") else 0.2) + 0.4 * (st.get("recent_count", 0) / max(1, len(members))))
    if (st.get("query_concentration") or 0) >= 0.6:
        saturation = 0.5  # unknown: cluster size is an echo of our own search query
    else:
        saturation = _clip01((len(members) / max(1, corpus_size)) * 3 * min(1.0, len(creators) / 8))
    cr = [m["comment_rate"] for m in members if m.get("comment_rate") is not None]
    lr = [m["like_rate"] for m in members if m.get("like_rate") is not None]
    share = _clip01((median(cr) * 200 if cr else 0) * 0.5 + (median(lr) * 15 if lr else 0) * 0.5) if (cr or lr) else 0.3
    feas = cluster.get("feasibility", 0.5)
    return {"outperformance": outp, "momentum": mom, "independence": indep, "novelty": novelty,
            "saturation": saturation, "shareability": share, "feasibility": feas, "own_history": own.mean()}


def score(c: dict[str, float]) -> float:
    return sum(WEIGHTS[k] * v for k, v in c.items())


def feasibility_from_format(format_desc: dict | None) -> float:
    """Heuristic: fast-cut / location-heavy formats cost more to produce originally."""
    if not format_desc:
        return 0.5
    f = 0.6
    for t in format_desc.get("traits", []):
        if t["feature"] == "cuts_per_10s" and t["z"] > 0.8:
            f -= 0.15
        if t["feature"] in ("speech_ratio", "words_per_s") and t["z"] > 0.8:
            f += 0.05  # voice-over formats are cheap with TTS or a narrator
        if t["feature"] == "text_on_screen_ratio" and t["z"] > 0.5:
            f += 0.1
    return _clip01(f)


def rank(
    topic_clusters: list[dict],
    format_clusters: list[dict],
    corpus_size: int,
    own_history: dict[str, OwnHistory] | None = None,
    n_slots: int = 6,
    explore_share: float = 0.34,
    n_boot: int = 300,
    seed: int = 0,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    own_history = own_history or {}
    fmt_of = {}
    for fc in format_clusters:
        for m in fc["members"]:
            fmt_of[f"{m['platform']}:{m['video_id']}"] = fc
    candidates = []
    for tc in topic_clusters:
        if tc["cluster_id"].endswith("_noise"):
            continue
        groups: dict[str, list[dict]] = {}
        for m in tc["members"]:
            fc = fmt_of.get(f"{m['platform']}:{m['video_id']}")
            key = fc["cluster_id"] if fc and not fc["cluster_id"].endswith("_noise") else "*"
            groups.setdefault(key, []).append(m)
        groups["*"] = tc["members"]  # the topic as a whole is always a candidate
        for fkey, members in groups.items():
            if len(members) < 2:
                continue
            fc = next((f for f in format_clusters if f["cluster_id"] == fkey), None)
            cl = {**tc, "feasibility": feasibility_from_format(fc["description"] if fc else None)}
            own = own_history.get(f"{tc['cluster_id']}|{fkey}", OwnHistory())
            comp = components(members, cl, corpus_size, own)
            boots = []
            for _ in range(n_boot):
                sample = [rng.choice(members) for _ in members]
                bc = components(sample, cl, corpus_size, own)
                bc["own_history"] = own.sample(rng)
                boots.append(score(bc))
            boots.sort()
            candidates.append({
                "topic_cluster": tc["cluster_id"], "topic_label": tc["label"],
                "format_cluster": fkey, "format_label": fc["label"] if fc else "any format",
                "score": round(score(comp), 4),
                "score_low": round(boots[int(0.1 * n_boot)], 4), "score_high": round(boots[int(0.9 * n_boot)], 4),
                "components": {k: round(v, 3) for k, v in comp.items()},
                "evidence": [{"platform": m["platform"], "video_id": m["video_id"], "views": m.get("views"),
                              "log_outperformance": m.get("log_outperformance"), "access_level": m.get("access_level"),
                              "ad_suspect": bool(m.get("ad_suspect"))}
                             for m in sorted(members, key=lambda m: -m["popularity"])[:8]],
                "n_members": len(members), "_boots": boots,
                "caveats": [c for c in (
                    "query echo: cluster size reflects our search, not prevalence"
                    if (tc["stats"].get("query_concentration") or 0) >= 0.6 else None,
                    f"{sum(1 for m in members if m.get('ad_suspect'))} member(s) look ad-served (excluded as evidence)"
                    if any(m.get("ad_suspect") for m in members) else None,
                    "single creator: not independent repetition" if len({m.get('creator_id') for m in members}) == 1 else None,
                    "no creator baselines: outperformance falls back to absolute popularity"
                    if not any(m.get("log_outperformance") is not None and not m.get("ad_suspect") for m in members)
                    else None,
                ) if c],
            })
    candidates.sort(key=lambda c: -c["score"])
    n_exploit = max(1, round(n_slots * (1 - explore_share)))
    chosen, seen_topics = [], set()
    for c in candidates:  # exploit: best expected score, one per topic for diversity
        if len(chosen) >= n_exploit:
            break
        if c["topic_cluster"] not in seen_topics:
            chosen.append({**c, "is_exploration": False, "selection": "exploit: highest expected score"})
            seen_topics.add(c["topic_cluster"])
    rest = [c for c in candidates if c["topic_cluster"] not in seen_topics]
    while len(chosen) < n_slots and rest:  # explore: Thompson draw favours uncertain, under-tested cells
        draws = [(rng.choice(c["_boots"]) + 0.05 * (c["score_high"] - c["score_low"]), c) for c in rest]
        _, pick = max(draws, key=lambda d: d[0])
        chosen.append({**pick, "is_exploration": True, "selection": "explore: Thompson draw over bootstrap"})
        seen_topics.add(pick["topic_cluster"])
        rest = [c for c in rest if c["topic_cluster"] not in seen_topics]
    for c in chosen:
        c.pop("_boots", None)
    return chosen
