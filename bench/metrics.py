"""Scoring. Every number here is computed from ids, never judged.

Retrieval:
  recall@k      share of expected memories found in the top k
  mrr           mean reciprocal rank of the first expected memory
  hit@1         share of positive queries whose top result is expected
Honesty:
  stale_serve   share of queries with a superseded memory in the top k
  leakage       share of scope-restricted queries serving a memory from an
                unloaded scope
  negative_serve  share of queries with no right answer that returned anything
  negative_confident  ...that returned something the system marked confident,
                which is the confident-liar case; only for systems that label
Calibration (for belief, from Stage 2 on):
  brier, ece    over (probability, outcome) pairs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean

from bench.fixture import Fixture, Query


@dataclass(frozen=True)
class Hit:
    id: str
    score: float
    label: str = ""
    confident: bool | None = None   # None: the system makes no such claim


@dataclass
class QueryResult:
    query: Query
    hits: list[Hit]
    rank: int | None            # 1-based rank of the first expected hit
    recall: float | None        # None for negative queries
    stale_served: list[str]
    leaked: list[str]
    forbidden_served: list[str]
    stale_applicable: bool
    scope_restricted: bool


@dataclass
class FixtureScore:
    fixture: str
    k: int
    results: list[QueryResult] = field(default_factory=list)

    def summary(self) -> dict:
        pos = [r for r in self.results if not r.query.is_negative]
        neg = [r for r in self.results if r.query.is_negative]
        stale = [r for r in self.results if r.stale_applicable]
        scoped = [r for r in self.results if r.scope_restricted]
        labels = any(h.confident is not None for r in self.results for h in r.hits)
        return {
            "queries": len(self.results),
            "positives": len(pos),
            "negatives": len(neg),
            f"recall@{self.k}": _mean([r.recall for r in pos]),
            "mrr": _mean([1 / r.rank if r.rank else 0.0 for r in pos]),
            "hit@1": _mean([1.0 if r.rank == 1 else 0.0 for r in pos]),
            "stale_serve": _mean([1.0 if r.stale_served else 0.0 for r in stale]),
            "leakage": _mean([1.0 if r.leaked else 0.0 for r in scoped]),
            "negative_serve": _mean([1.0 if r.hits else 0.0 for r in neg]),
            "negative_confident": (_mean([1.0 if any(h.confident for h in r.hits) else 0.0
                                          for r in neg]) if labels else None),
            "forbidden_serve": _mean([1.0 if r.forbidden_served else 0.0
                                      for r in self.results if r.query.forbid]),
        }


def _mean(xs: list[float]) -> float | None:
    return round(fmean(xs), 4) if xs else None


def score_query(fx: Fixture, q: Query, hits: list[Hit], k: int) -> QueryResult:
    top = hits[:k]
    ids = [h.id for h in top]
    rank = next((i + 1 for i, mid in enumerate(ids) if mid in q.expect), None)
    recall = (len(set(ids) & set(q.expect)) / len(q.expect)) if q.expect else None
    stale = fx.stale_ids(q)
    loaded = fx.loaded_scopes(q)
    return QueryResult(
        query=q, hits=top, rank=rank, recall=recall,
        stale_served=[m for m in ids if m in stale],
        leaked=[m for m in ids if fx.memory(m).scope not in loaded],
        forbidden_served=[m for m in ids if m in q.forbid],
        stale_applicable=bool(stale),
        scope_restricted=loaded != fx.scopes,
    )


def brier(pairs: list[tuple[float, bool]]) -> float | None:
    if not pairs:
        return None
    return round(fmean((p - float(o)) ** 2 for p, o in pairs), 4)


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float | None:
    """Expected calibration error with equal-width bins."""
    if not pairs:
        return None
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, o in pairs:
        buckets[min(int(p * bins), bins - 1)].append((p, o))
    n = len(pairs)
    err = sum(len(b) / n * abs(fmean(p for p, _ in b) - fmean(float(o) for _, o in b))
              for b in buckets if b)
    return round(err, 4)
