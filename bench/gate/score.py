"""Score the gate: embedding-only (E) against Jev (J), on labels people wrote.

Pre-registered (bench/gate/README.md). Turns are split into dev and test by a recorded seed; no turn
has candidates on both sides. Each arm's threshold is tuned on dev alone, to the HIGHEST threshold
that still keeps at least 90% of the dev items labelled "constrains". On test, both arms decide every
candidate, so the comparison is paired:

  primary    false injections: on test items labelled "doesn't constrain", how many each arm
             injects. Exact two-sided McNemar on the paired decisions, alpha 0.05.
  secondary  kept "constrains" items (McNemar the same way), AUROC of each score, base rate.
"""

from __future__ import annotations

import random
from math import comb


def split(turns: list[str], seed: int) -> tuple[set[str], set[str]]:
    """Half the turns to dev, half to test, by seed. Every turn lands on exactly one side."""
    order = sorted(set(turns))
    random.Random(seed).shuffle(order)
    half = len(order) // 2
    return set(order[:half]), set(order[half:])


def threshold(scores: list[float], labels: list[bool], keep: float = 0.9) -> float:
    """The highest t such that score >= t keeps at least `keep` of the positives."""
    pos = sorted((s for s, y in zip(scores, labels) if y), reverse=True)
    if not pos:
        raise ValueError("no positives to tune on")
    need = -(-len(pos) * keep // 1)           # ceil without float error at the edge
    return pos[int(need) - 1]


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    """Exact two-sided McNemar for paired decisions: b01 = a only, b10 = b only."""
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    if n == 0:
        return {"only_a": 0, "only_b": 0, "p": 1.0}
    k = min(only_a, only_b)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"only_a": only_a, "only_b": only_b, "p": p}


def auroc(scores: list[float], labels: list[bool]) -> float | None:
    """Probability a random positive outscores a random negative (ties count half)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def evaluate(rows: list[dict], seed: int, keep: float = 0.9) -> dict:
    """rows: {"turn", "cosine", "p", "constrains": bool}. Returns the pre-registered numbers."""
    dev_turns, test_turns = split([r["turn"] for r in rows], seed)
    dev = [r for r in rows if r["turn"] in dev_turns]
    test = [r for r in rows if r["turn"] in test_turns]
    y_dev = [r["constrains"] for r in dev]
    t_e = threshold([r["cosine"] for r in dev], y_dev, keep)
    t_j = threshold([r["p"] for r in dev], y_dev, keep)
    e = [r["cosine"] >= t_e for r in test]
    j = [r["p"] >= t_j for r in test]
    y = [r["constrains"] for r in test]
    neg = [i for i, v in enumerate(y) if not v]
    pos = [i for i, v in enumerate(y) if v]
    return {
        "n_dev": len(dev), "n_test": len(test), "base_rate_test": sum(y) / len(y) if y else None,
        "t_e": t_e, "t_j": t_j,
        "false_inject": {"E": sum(e[i] for i in neg), "J": sum(j[i] for i in neg), "of": len(neg),
                         "mcnemar": mcnemar([e[i] for i in neg], [j[i] for i in neg])},
        "kept": {"E": sum(e[i] for i in pos), "J": sum(j[i] for i in pos), "of": len(pos),
                 "mcnemar": mcnemar([e[i] for i in pos], [j[i] for i in pos])},
        "auroc": {"E": auroc([r["cosine"] for r in test], y), "J": auroc([r["p"] for r in test], y)},
    }


def kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's kappa for two labellers on the same items."""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)
