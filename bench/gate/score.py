"""Score the gate: embedding-only (E) against Jev (J), on labels people wrote.

Pre-registered in /home/foil/work/reviews/tapestry-jev-gate-plan.md. Labels are three-way:
"constrains", "informs" (true and useful, but doesn't constrain: a path, a host name), or
"irrelevant". Jev answers the binary question "does it constrain?".

Turns are split into dev and test by a recorded seed; no turn has candidates on both sides. Each
arm's threshold is tuned on dev alone: the HIGHEST t such that score >= t keeps at least 90% of the
dev items labelled "constrains". Tie rule: a score equal to the threshold is injected. On test,
both arms decide every candidate, so the comparison is paired:

  primary      among test items NOT labelled "constrains", did E inject it and did J? Exact
               two-sided McNemar on those pairs: false injection at matched dev recall. alpha 0.05.
  secondary    achieved test recall of "constrains" per arm (paired McNemar too), the share of
               "informs" each arm drops, AUROC of each score, the base rate.
  descriptive  each arm's share of its own injected set that doesn't constrain. The denominators
               differ per arm, so it is never tested.
"""

from __future__ import annotations

import random
from math import comb

LABELS = ("constrains", "informs", "irrelevant")


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
    """Exact two-sided McNemar for paired decisions: only_a = a alone, only_b = b alone."""
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


def _rate(x: int, of: int) -> float | None:
    return x / of if of else None


def evaluate(rows: list[dict], seed: int, keep: float = 0.9, exclude: frozenset = frozenset()) -> dict:
    """rows: {"turn", "cosine", "p", "label" in LABELS}. Returns the pre-registered numbers.

    `exclude` lists turns removed before the split, by a frozen record written before any Jev
    answer (frozen/exclude.json: duplicate turns found after the corpus was frozen).
    """
    rows = [r for r in rows if r["turn"] not in exclude]
    for r in rows:
        if r.get("label") not in LABELS:
            raise ValueError(f"unlabelled or unknown label: {r.get('label')!r}")
    dev_turns, test_turns = split([r["turn"] for r in rows], seed)
    dev = [r for r in rows if r["turn"] in dev_turns]
    test = [r for r in rows if r["turn"] in test_turns]
    y_dev = [r["label"] == "constrains" for r in dev]
    t_e = threshold([r["cosine"] for r in dev], y_dev, keep)
    t_j = threshold([r["p"] for r in dev], y_dev, keep)
    e = [r["cosine"] >= t_e for r in test]
    j = [r["p"] >= t_j for r in test]
    lab = [r["label"] for r in test]
    y = [v == "constrains" for v in lab]
    neg = [i for i, v in enumerate(lab) if v != "constrains"]
    pos = [i for i, v in enumerate(lab) if v == "constrains"]
    inf = [i for i, v in enumerate(lab) if v == "informs"]
    return {
        "n_dev": len(dev), "n_test": len(test), "dev_positives": sum(y_dev),
        "base_rate_test": _rate(sum(y), len(y)),
        "labels_test": {k: lab.count(k) for k in LABELS},
        "t_e": t_e, "t_j": t_j,
        "false_inject": {"E": sum(e[i] for i in neg), "J": sum(j[i] for i in neg), "of": len(neg),
                         "mcnemar": mcnemar([e[i] for i in neg], [j[i] for i in neg])},
        "recall_test": {"E": _rate(sum(e[i] for i in pos), len(pos)),
                        "J": _rate(sum(j[i] for i in pos), len(pos)), "of": len(pos),
                        "mcnemar": mcnemar([e[i] for i in pos], [j[i] for i in pos])},
        "informs_dropped": {"E": _rate(sum(not e[i] for i in inf), len(inf)),
                            "J": _rate(sum(not j[i] for i in inf), len(inf)), "of": len(inf)},
        "auroc": {"E": auroc([r["cosine"] for r in test], y), "J": auroc([r["p"] for r in test], y)},
        "descriptive_share_not_constraining_of_injected": {
            "E": _rate(sum(e[i] for i in neg), sum(e)), "J": _rate(sum(j[i] for i in neg), sum(j))},
        "descriptive_E_at_J_recall": _e_at_recall([test[i]["cosine"] for i in pos],
                                                  [test[i]["cosine"] for i in neg],
                                                  sum(j[i] for i in pos)),
        "descriptive_inject_all": {"false_inject": len(neg), "recall": 1.0 if pos else None},
        # Rev 5: constrains is too rare to test, so every one either arm drops is named (test half).
        "constrains_dropped": {"E": [test[i].get("item", test[i]["turn"]) for i in pos if not e[i]],
                               "J": [test[i].get("item", test[i]["turn"]) for i in pos if not j[i]]},
    }


def _e_at_recall(pos_scores: list[float], neg_scores: list[float], k: int) -> dict | None:
    """E's false injections at the highest cosine that still keeps k test positives: E moved to
    J's achieved test recall, so a J win can't come from a lower operating point (Wren). Test data,
    so this is descriptive and never decides anything."""
    if not pos_scores or k == 0:
        return None
    t = sorted(pos_scores, reverse=True)[k - 1]
    return {"threshold": t, "kept": sum(s >= t for s in pos_scores),
            "false_inject": sum(s >= t for s in neg_scores)}


# The pre-registered verdict. Frozen before any Jev answer exists; a change after one does voids it.
ALPHA = 0.05
MAX_RECALL_GAP = 0.05
MAX_P95_SECONDS = 1.0


def p95(seconds: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    if not seconds:
        raise ValueError("no latencies")
    s = sorted(seconds)
    rank = -(-len(s) * 95 // 100)            # ceil(0.95 n), without float error
    return s[rank - 1]


def verdict(result: dict, latencies: list[float]) -> dict:
    """PASS only if all three hold on test; anything else, including no positives, is NULL.

    1. J injects fewer non-constraining items than E, at p < ALPHA (McNemar is two-sided, so
       the direction is checked here, not assumed).
    2. J's test recall of "constrains" is no more than MAX_RECALL_GAP below E's.
    3. Jev's p95 latency is under MAX_P95_SECONDS, measured from the machine that ran it.
    """
    fi, rc = result["false_inject"], result["recall_test"]
    if not rc["of"]:
        return {"verdict": "NULL", "why": ["no positives on test"]}
    why = []
    if not (fi["J"] < fi["E"] and fi["mcnemar"]["p"] < ALPHA):
        why.append(f"false injection: J {fi['J']} vs E {fi['E']}, p={fi['mcnemar']['p']:.3g}")
    if rc["J"] < rc["E"] - MAX_RECALL_GAP:
        why.append(f"recall: J {rc['J']:.3f} vs E {rc['E']:.3f}")
    lat = p95(latencies)
    if not lat < MAX_P95_SECONDS:
        why.append(f"latency: p95 {lat:.2f}s")
    dropped = result["constrains_dropped"]
    j_only = [i for i in dropped["J"] if i not in dropped["E"]]
    out = {"verdict": "NULL" if why else "PASS", "why": why, "p95_seconds": lat,
           "constrains_dropped": dropped, "dropped_by_J_only": j_only}
    if not why and j_only:
        out["verdict"] = "PASS, with a constraint dropped"   # never a bare PASS (rev 5)
    return out


def agreement(a: list[str], b: list[str]) -> dict:
    """Two labellers on the same items: percent agreement and Cohen's kappa, three-way and on the
    binary "constrains" split. Kappa collapses on a skewed base rate, so both are reported."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal, non-empty label lists")

    def kappa(x, y, cats):
        n = len(x)
        po = sum(p == q for p, q in zip(x, y)) / n
        pe = sum((x.count(c) / n) * (y.count(c) / n) for c in cats)
        return 1.0 if pe == 1 else (po - pe) / (1 - pe)

    ab = [v == "constrains" for v in a]
    bb = [v == "constrains" for v in b]
    return {"n": len(a),
            "percent_3way": sum(p == q for p, q in zip(a, b)) / len(a),
            "kappa_3way": kappa(a, b, LABELS),
            "percent_binary": sum(p == q for p, q in zip(ab, bb)) / len(a),
            "kappa_binary": kappa(ab, bb, (True, False))}
