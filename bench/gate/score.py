"""Score the gate: embedding-only (E) against Jev (J), on labels people wrote.

Pre-registered in /home/foil/work/reviews/tapestry-jev-gate-plan-3.md, as amended by revs 4, 5, 6
and 7 (the *-amendment.md files beside it). Labels are three-way: "constrains", "informs" (true and
bears on the message, but a reply that ignored it would still be correct) or "irrelevant"
(unrelated, or stale/superseded for this turn; labellers mark the latter `"stale": true`).

The gate is a RELEVANCE gate (rev 4): POSITIVE is constrains + informs. Jev's arm uses its
"bears on" Noul (jev.py P_PRIMARY). "Constrains" is descriptive only, and every constrains item an
arm drops on test is named (rev 5).

Turns are split into dev and test by a recorded seed; no turn has candidates on both sides. Each
arm's threshold is tuned on dev alone: the HIGHEST t such that score >= t keeps at least 90% of the
dev POSITIVE items. Tie rule: a score equal to the threshold is injected. On test, both arms decide
every candidate, so the comparison is paired:

  primary      among test items labelled irrelevant, did E inject it and did J? Exact two-sided
               McNemar on those pairs; the direction is checked in verdict(). Stale items are
               excluded (rev 5); report() also gives the including-stale secondary.
  power        discordant pairs and their split, before the p (rev 6). Five or fewer is
               "NO DECISION: underpowered", never a null.
  secondary    achieved test recall of POSITIVE per arm, injection rates, AUROC, context not
               injected (items and chars), the constrains rows, E at J's achieved recall, and
               inject-all. Of these, only the recall gap decides anything (in verdict()).
"""

from __future__ import annotations

import random
from math import comb

LABELS = ("constrains", "informs", "irrelevant")
# The positive class of the gate. Frozen (rev 4); every tuned or tested quantity reads it.
POSITIVE = frozenset({"constrains", "informs"})


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


def merge(a: dict[str, dict], b: dict[str, dict]) -> tuple[dict[str, dict], dict]:
    """One label per item from two labellers (item -> {"label", "stale"?}). Rev 7.

    An item only one labeller saw keeps that label. On the overlap: if both put it on the same
    side of the gate (relevant or irrelevant), it's kept; it's "constrains" if either said so (the
    rev 5 guard should over-count, not miss), and stale if either marked it. If they put it on
    opposite sides, it's DROPPED from scoring and counted. No adjudication once labels exist."""
    out, drop = {}, []
    for item in sorted(set(a) | set(b)):
        x, y = a.get(item), b.get(item)
        if x is None or y is None:
            z = x or y
            out[item] = {"label": z["label"], "stale": bool(z.get("stale"))}
            continue
        if (x["label"] in POSITIVE) != (y["label"] in POSITIVE):
            drop.append(item)
            continue
        lab = x["label"]
        if lab in POSITIVE:
            lab = "constrains" if "constrains" in (x["label"], y["label"]) else "informs"
        out[item] = {"label": lab, "stale": bool(x.get("stale") or y.get("stale"))}
    return out, {"overlap": len(set(a) & set(b)), "dropped_disagree": len(drop), "dropped": drop}


def evaluate(rows: list[dict], seed: int, keep: float = 0.9, exclude: frozenset = frozenset(),
             stale: str = "exclude") -> dict:
    """rows: {"turn", "item"?, "cosine", "p", "label" in LABELS, "stale"?, "memory"?}.

    `exclude` lists turns removed before the split (frozen/exclude.json, written before any Jev
    answer). The split is drawn over every remaining turn BEFORE stale items are filtered, so the
    stale-excluded primary and the including-stale secondary share one split.
    """
    if stale not in ("exclude", "include"):
        raise ValueError(f"stale must be 'exclude' or 'include', not {stale!r}")
    rows = [r for r in rows if r["turn"] not in exclude]
    for r in rows:
        if r.get("label") not in LABELS:
            raise ValueError(f"unlabelled or unknown label: {r.get('label')!r}")
        if r.get("stale") and r["label"] != "irrelevant":
            raise ValueError(f"stale marks irrelevant items only: {r.get('item')!r}")
    dev_turns, test_turns = split([r["turn"] for r in rows], seed)
    n_stale = sum(1 for r in rows if r.get("stale"))
    if stale == "exclude":
        rows = [r for r in rows if not r.get("stale")]
    dev = [r for r in rows if r["turn"] in dev_turns]
    test = [r for r in rows if r["turn"] in test_turns]
    y_dev = [r["label"] in POSITIVE for r in dev]
    t_e = threshold([r["cosine"] for r in dev], y_dev, keep)
    t_j = threshold([r["p"] for r in dev], y_dev, keep)
    e = [r["cosine"] >= t_e for r in test]
    j = [r["p"] >= t_j for r in test]
    lab = [r["label"] for r in test]
    y = [v in POSITIVE for v in lab]
    pos = [i for i, v in enumerate(y) if v]
    neg = [i for i, v in enumerate(y) if not v]
    con = [i for i, v in enumerate(lab) if v == "constrains"]
    ident = [r.get("item", r["turn"]) for r in test]
    chars = [len(r["memory"]) if "memory" in r else None for r in test]
    fi_mc = mcnemar([e[i] for i in neg], [j[i] for i in neg])

    def rates(d):
        return {"all": _rate(sum(d), len(d)), "relevant": _rate(sum(d[i] for i in pos), len(pos)),
                "irrelevant": _rate(sum(d[i] for i in neg), len(neg))}

    def not_injected(d):
        c = [chars[i] for i in neg if not d[i]]
        return {"items": len(c), "chars": None if None in c else sum(c)}

    return {
        "stale": stale, "stale_marked": n_stale,
        "n_dev": len(dev), "n_test": len(test), "dev_positives": sum(y_dev),
        "base_rate_test": _rate(sum(y), len(y)),
        "labels_test": {k: lab.count(k) for k in LABELS},
        "t_e": t_e, "t_j": t_j,
        "false_inject": {"E": sum(e[i] for i in neg), "J": sum(j[i] for i in neg), "of": len(neg),
                         "mcnemar": fi_mc},
        "discordant": {"n": fi_mc["only_a"] + fi_mc["only_b"], "E_only": fi_mc["only_a"],
                       "J_only": fi_mc["only_b"]},
        "recall_test": {"E": _rate(sum(e[i] for i in pos), len(pos)),
                        "J": _rate(sum(j[i] for i in pos), len(pos)), "of": len(pos),
                        "mcnemar": mcnemar([e[i] for i in pos], [j[i] for i in pos])},
        "injection_rate": {"E": rates(e), "J": rates(j)},
        "context_not_injected": {"E": not_injected(e), "J": not_injected(j)},
        "auroc": {"E": auroc([r["cosine"] for r in test], y), "J": auroc([r["p"] for r in test], y)},
        "descriptive_share_irrelevant_of_injected": {
            "E": _rate(sum(e[i] for i in neg), sum(e)), "J": _rate(sum(j[i] for i in neg), sum(j))},
        "descriptive_E_at_J_recall": _e_at_recall([test[i]["cosine"] for i in pos],
                                                  [test[i]["cosine"] for i in neg],
                                                  sum(j[i] for i in pos)),
        "descriptive_inject_all": {"false_inject": len(neg), "recall": 1.0 if pos else None},
        # Rev 5: constrains is too rare to test, so its recall is descriptive and every constrains
        # item either arm drops on test is named.
        "constrains": {"of": len(con), "recall": {"E": _rate(sum(e[i] for i in con), len(con)),
                                                  "J": _rate(sum(j[i] for i in con), len(con))}},
        "constrains_dropped": {"E": [ident[i] for i in con if not e[i]],
                               "J": [ident[i] for i in con if not j[i]]},
    }


def report(rows: list[dict], seed: int, latencies: list[float],
           exclude: frozenset = frozenset(), merged: dict | None = None) -> dict:
    """The pre-registered report: the stale-excluded primary decides; including-stale is shown.
    `merged` is merge()'s info; its dropped_disagree is reported beside the result, because
    dropping the hardest overlap items flatters both arms alike (Wren b116e8ff)."""
    primary = evaluate(rows, seed, exclude=exclude, stale="exclude")
    secondary = evaluate(rows, seed, exclude=exclude, stale="include")
    return {"verdict": verdict(primary, latencies), "primary": primary, "secondary": secondary,
            "stale_changes_nothing": primary["stale_marked"] == 0,
            "dropped_disagree": None if merged is None else merged["dropped_disagree"]}


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
MIN_DISCORDANT = 6            # rev 6 §3: with 5 or fewer, p < 0.05 is impossible
E_SATURATED = 0.95            # rev 6 §4: E's irrelevant injection rate at which the wording changes
MIN_KAPPA = 0.6               # rev 4: binary relevant/irrelevant agreement, or stop and reword


def p95(seconds: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    if not seconds:
        raise ValueError("no latencies")
    s = sorted(seconds)
    rank = -(-len(s) * 95 // 100)            # ceil(0.95 n), without float error
    return s[rank - 1]


def verdict(result: dict, latencies: list[float]) -> dict:
    """PASS only if all three hold on test; otherwise NULL, or NO DECISION when underpowered.

    1. J injects fewer irrelevant items than E, at p < ALPHA (McNemar is two-sided, so the
       direction is checked here, not assumed).
    2. J's test recall of POSITIVE is no more than MAX_RECALL_GAP below E's.
    3. Jev's p95 latency is under MAX_P95_SECONDS, measured from the machine that ran it.
    The verdict string carries the rev 5 and rev 6 qualifiers, so a reader never sees a bare PASS
    that isn't one.
    """
    fi, rc, dc = result["false_inject"], result["recall_test"], result["discordant"]
    dropped = result["constrains_dropped"]
    j_only = [i for i in dropped["J"] if i not in dropped["E"]]
    base = {"discordant": dc, "constrains_dropped": dropped, "dropped_by_J_only": j_only}
    if not rc["of"]:
        return {"verdict": "NULL", "why": ["no relevant items on test"], **base}
    if dc["n"] < MIN_DISCORDANT:
        return {"verdict": "NO DECISION: underpowered",
                "why": [f"{dc['n']} discordant pairs (E only {dc['E_only']}, J only {dc['J_only']})"],
                **base}
    why = []
    if not (fi["J"] < fi["E"] and fi["mcnemar"]["p"] < ALPHA):
        why.append(f"false injection: J {fi['J']} vs E {fi['E']}, p={fi['mcnemar']['p']:.3g}")
    if rc["J"] < rc["E"] - MAX_RECALL_GAP:
        why.append(f"recall: J {rc['J']:.3f} vs E {rc['E']:.3f}")
    lat = p95(latencies)
    if not lat < MAX_P95_SECONDS:
        why.append(f"latency: p95 {lat:.2f}s")
    if why:
        return {"verdict": "NULL", "why": why, "p95_seconds": lat, **base}
    e_rates = result["injection_rate"]["E"]
    e_irr = e_rates["irrelevant"]
    v = "PASS"
    if e_irr is not None and e_irr >= E_SATURATED:
        v = (f"PASS: J gates where cosine cannot (cosine at 90% relevant recall injects "
             f"{e_irr:.0%} of irrelevant)")
    if j_only:
        v += ", with a constraint dropped"          # never a bare PASS (rev 5)
    # Rev 7: every PASS is printed with these three beside it, saturated or not.
    line = (f"{v} [E injects {e_rates['all']:.1%} of test items, {e_irr:.1%} of irrelevant; "
            f"inject-all would inject all {result['descriptive_inject_all']['false_inject']} irrelevant]")
    return {"verdict": v, "line": line, "why": [], "p95_seconds": lat, **base}


def _kappa(x: list, y: list, cats) -> float:
    n = len(x)
    po = sum(p == q for p, q in zip(x, y)) / n
    pe = sum((x.count(c) / n) * (y.count(c) / n) for c in cats)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def agreement(a: list[str], b: list[str], stale_a: list[bool] | None = None,
              stale_b: list[bool] | None = None) -> dict:
    """Two labellers on the same items. The gate (rev 4) is kappa on POSITIVE vs irrelevant,
    >= MIN_KAPPA. Three-way, constrains-vs-rest and stale agreement are reported and gate nothing.
    Stale agreement is computed on the items both labellers call irrelevant (rev 5)."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal, non-empty label lists")
    ab = [v in POSITIVE for v in a]
    bb = [v in POSITIVE for v in b]
    ac = [v == "constrains" for v in a]
    bc = [v == "constrains" for v in b]
    out = {"n": len(a),
           "percent_3way": sum(p == q for p, q in zip(a, b)) / len(a),
           "kappa_3way": _kappa(a, b, LABELS),
           "percent_binary": sum(p == q for p, q in zip(ab, bb)) / len(a),
           "kappa_binary": _kappa(ab, bb, (True, False)),
           "percent_constrains": sum(p == q for p, q in zip(ac, bc)) / len(a),
           "kappa_constrains": _kappa(ac, bc, (True, False))}
    out["gate_ok"] = round(out["kappa_binary"], 9) >= MIN_KAPPA     # 0.6 computes as 0.6000000000000001
    if stale_a is not None and stale_b is not None:
        both = [i for i in range(len(a)) if a[i] == "irrelevant" and b[i] == "irrelevant"]
        sa = [bool(stale_a[i]) for i in both]
        sb = [bool(stale_b[i]) for i in both]
        out["stale"] = {"n_both_irrelevant": len(both),
                        "percent": _rate(sum(p == q for p, q in zip(sa, sb)), len(both)),
                        "kappa": _kappa(sa, sb, (True, False)) if both else None}
    return out
