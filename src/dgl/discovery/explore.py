"""Adaptive, taxonomy-free query planning.

Search terms are never chosen from a list of content niches. They come from:

* `chart`     — the platform's region-wide popularity chart (no query at all),
* `exploit`   — hashtags / title n-grams that appear in *observed* outperformers,
* `explore`   — terms that appear in the corpus but are under-sampled
                (UCB bonus), so new clusters can surface,
* `random`    — random probes built from short, topic-neutral high-frequency
                tokens; a known technique for approximately unbiased platform
                sampling. Their yield is tracked like any other arm.

A fixed share of the budget (default 35%) always goes to explore + random so
the engine keeps looking outside previously successful clusters.
"""
from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Topic-neutral probes: function words, interjections, numerals, single syllables.
# They carry no content category; they exist only to randomise the search frontier.
NEUTRAL_PROBES = {
    "ko": ["오늘", "진짜", "이거", "그냥", "ㅋㅋ", "근데", "처음", "다시", "하나", "우리", "왜", "결국",
           "1", "2", "3", "가", "나", "다", "마", "사"],
    "en": ["the", "this", "why", "when", "first", "again", "one", "day", "how", "what", "a", "i"],
    "ja": ["これ", "今日", "なぜ", "初めて"],
}
TOKEN_RE = re.compile(r"[가-힣]{2,}|[a-zA-Z]{3,}|#[\w가-힣]+", re.UNICODE)
STOP = {"shorts", "#shorts", "short", "youtube", "subscribe", "구독", "좋아요", "the", "and", "for", "with",
        "#short", "#fyp", "#viral", "#reels", "#쇼츠", "쇼츠", "릴스"}


@dataclass
class Arm:
    term: str
    kind: str
    pulls: int = 0
    reward_sum: float = 0.0

    @property
    def mean(self) -> float:
        return self.reward_sum / self.pulls if self.pulls else 0.0


@dataclass
class QueryPlanner:
    explore_share: float = 0.35
    seed: int | None = None
    arms: dict[str, Arm] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    @staticmethod
    def terms_from(videos: list[dict]) -> Counter:
        c: Counter = Counter()
        for v in videos:
            toks = set(TOKEN_RE.findall(f"{v.get('title') or ''} {' '.join('#' + h for h in v.get('hashtags') or [])}"))
            c.update(t.lower() for t in toks if t.lower() not in STOP)
        return c

    def update_corpus(self, scored_videos: list[tuple[dict, float]]) -> None:
        """Register terms observed in the corpus, keyed by how well their videos performed."""
        self._term_perf = defaultdict(list)
        for v, score in scored_videos:
            for t in self.terms_from([v]):
                self.arms.setdefault(t, Arm(t, "corpus"))  # reward accrues only from pulls
                self._term_perf[t].append(score)

    def record_yield(self, term: str, new_scores: list[float]) -> None:
        arm = self.arms.setdefault(term, Arm(term, "probe"))
        arm.pulls += 1
        arm.reward_sum += (sum(new_scores) / len(new_scores)) if new_scores else 0.0

    def plan(self, n_queries: int, lang: str = "ko") -> list[tuple[str, str]]:
        """Return (term, reason) pairs; reason ∈ exploit|explore|random."""
        perf = getattr(self, "_term_perf", {})
        n_explore = max(1, round(n_queries * self.explore_share)) if n_queries > 1 else 0
        n_exploit = n_queries - n_explore
        chosen: list[tuple[str, str]] = []

        # exploit: terms shared by >= 2 videos with the best median performance
        ranked = sorted(
            ((t, sorted(s)[len(s) // 2]) for t, s in perf.items() if len(s) >= 2),
            key=lambda x: -x[1],
        )
        for t, _ in ranked[:n_exploit]:
            chosen.append((t, "exploit"))

        # explore: UCB over under-sampled corpus terms + random neutral probes
        total = sum(a.pulls for a in self.arms.values()) + 1
        pool = [a for a in self.arms.values() if a.term not in {c[0] for c in chosen}]
        ucb = sorted(pool, key=lambda a: -(a.mean + math.sqrt(2 * math.log(total) / (a.pulls + 1))))
        n_rand = max(1, n_explore // 2) if n_explore else 0
        for a in ucb[: n_explore - n_rand]:
            chosen.append((a.term, "explore"))
        probes = NEUTRAL_PROBES.get(lang, NEUTRAL_PROBES["en"])
        # sample without replacement: a repeated probe re-spends quota on the same results
        used = {t for t, _ in chosen}
        fresh = [p for p in probes if p not in used]
        for p in self.rng.sample(fresh, min(len(fresh), n_queries - len(chosen))):
            chosen.append((p, "random"))
        return chosen[:n_queries]
