"""Which model should judge whether two memories conflict, and at what cost and latency?

    python -m bench.judge_compare OUT_DIR --n 30 --seed 20261020 --arms haiku,haiku-low[,jev --jev-key-file F]

Pairs come from LongMemEval's knowledge-update questions, whose haystack holds an old value and, later,
a new one (`has_answer` marks both). Each question's new evidence turn is the statement. Its candidates are every older evidence
turn plus the nearest other user turns at cosine 0.30 or better, ten in all, which is the shape
Mind.neighbours would hand a judge. An older evidence turn is a conflict. Any other candidate is
counted as no conflict, which the dataset doesn't promise: a candidate that really is a conflict
counts against the judge, so false conflicts are an upper bound.

Every arm sees the same statements and candidates. Latency is wall-clock per statement, with
requests made one at a time. Cost is what the service reports for the request.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from bench.gate.lme_update import eligible, turns
from bench.longmemeval import DEFAULT_PATH
from tapestry import judge as judges

K, FLOOR = 10, 0.30


def cases(items: list[dict], encode, n: int, seed: int, exclude: set[str] = frozenset()) -> list[dict]:
    import random
    import numpy as np
    pool = sorted((it for it in items if eligible(it) and it["question_id"] not in exclude),
                  key=lambda it: it["question_id"])
    chosen = sorted(random.Random(seed).sample(pool, min(n, len(pool))), key=lambda it: it["question_id"])
    out = []
    for item in chosen:
        ts = turns(item)
        vecs = encode([t["text"] for t in ts])
        ev = [i for i, t in enumerate(ts) if t["evidence"]]
        new, olds = ev[-1], ev[:-1]
        cos = vecs @ vecs[new]
        order = [int(i) for i in np.argsort(-cos) if i != new]
        near = [i for i in order if cos[i] >= FLOOR][:K]
        reached = [i for i in olds if i in near]
        # Every old evidence turn is always a candidate, so the judge is tested on each; the rest are
        # the nearest other turns, as Mind.neighbours would find them. How many old turns the
        # neighbour rule would have reached on its own is reported separately.
        rest = [i for i in order if i not in olds and cos[i] >= FLOOR][:max(0, K - len(olds))]
        ids = olds + rest
        random.Random(f"{seed}:{item['question_id']}").shuffle(ids)
        out.append({"question": item["question_id"], "statement": ts[new]["text"],
                    "candidates": [{"id": i, "text": ts[i]["text"], "conflict": i in olds} for i in ids],
                    "reached_by_neighbours": len(reached), "olds": len(olds)})
    return out


class JevArm:
    """Jev for comparison: the same request, with cost and time taken from the response."""
    def __init__(self, key_file: Path) -> None:
        self.stats: dict = {}
        base = judges.JevJudge(key_file)

        def send(body):
            t0 = time.monotonic()
            resp = base._http(body)
            gateway = (resp.get("provider_metadata") or {}).get("gateway") or {}
            self.stats = {"cost_usd": float(gateway.get("cost") or 0), "wall_s": time.monotonic() - t0,
                          "api_s": None,
                          "p": [float((resp.get("answers") or {}).get(f"c:{i}", {}).get("noul", -1))
                                for i in range(len(body["questions"]))]}
            return resp
        self.judge = judges.JevJudge(key_file, send=send)
        self.model = judges.MODEL
        self.prompt = judges.PROMPT

    def conflicts(self, s, o):
        return self.judge.conflicts(s, o)

    @property
    def last(self):
        return self.stats


def make_arm(name: str, jev_key: Path | None):
    if name == "haiku":
        return judges.ClaudeJudge("haiku", effort=None)
    if name == "haiku-low":
        return judges.ClaudeJudge("haiku", effort="low")
    if name == "jev":
        if not jev_key:
            raise SystemExit("--jev-key-file is needed for the jev arm")
        return JevArm(jev_key)
    raise SystemExit(f"unknown arm {name!r}")


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def run_arm(name: str, arm, cs: list[dict], out: Path) -> dict:
    rows, calls = [], []
    for c in cs:
        others = [(x["id"], x["text"]) for x in c["candidates"]]
        try:
            got = arm.conflicts(c["statement"], others)
        except Exception as e:
            rows.append({"question": c["question"], "error": f"{type(e).__name__}: {e}"})
            continue
        calls.append(dict(arm.last))
        rows.append({"question": c["question"],
                     "verdicts": {str(x["id"]): {"label": x["conflict"], "verdict": got[x["id"]],
                                                 **({"p": arm.last["p"][i]} if "p" in arm.last else {})}
                                  for i, x in enumerate(c["candidates"])}, "stats": dict(arm.last)})
    (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    pos = [v for r in rows for v in r.get("verdicts", {}).values() if v["label"]]
    neg = [v for r in rows for v in r.get("verdicts", {}).values() if not v["label"]]
    flagged = lambda vs, which: sum(v["verdict"] in which for v in vs)
    sweep = {}
    if pos and "p" in pos[0]:      # what other cut-offs would have given, for an arm that returns probabilities
        for t in (0.5, 0.6, 0.7, 0.8, 0.9):
            sweep[str(t)] = {"found": sum(v["p"] >= t for v in pos), "false": sum(v["p"] >= t for v in neg)}
    wall = [c["wall_s"] for c in calls]
    api = [c["api_s"] for c in calls if c.get("api_s")]
    cost = [c["cost_usd"] or 0 for c in calls]
    return {"arm": name, "model": arm.model, "statements": len(cs), "errors": sum("error" in r for r in rows),
            "positives_reached": len(pos), "old_turns_the_neighbour_rule_reaches": f"{sum(c['reached_by_neighbours'] for c in cs)}/{sum(c['olds'] for c in cs)}",
            "found_as_conflict": flagged(pos, {"conflict"}), "found_as_maybe": flagged(pos, {"maybe"}),
            "negatives": len(neg), "false_conflict": flagged(neg, {"conflict"}),
            "false_maybe": flagged(neg, {"maybe"}),
            "wall_s_p50": pct(wall, .5), "wall_s_p95": pct(wall, .95),
            "api_s_p50": pct(api, .5) if api else None, "api_s_p95": pct(api, .95) if api else None,
            "cut_offs": sweep, "cost_usd_total": sum(cost), "cost_usd_per_statement": statistics.mean(cost) if cost else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--data", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--arms", default="haiku")
    ap.add_argument("--jev-key-file", type=Path)
    ap.add_argument("--exclude-cases", type=Path, help="a cases.json whose questions are left out")
    a = ap.parse_args()
    from tapestry import embed
    a.out.mkdir(parents=True, exist_ok=True)
    cs_file = a.out / "cases.json"
    if cs_file.exists():
        cs = json.loads(cs_file.read_text())
    else:
        skip = {c["question"] for c in json.loads(a.exclude_cases.read_text())} if a.exclude_cases else set()
        cs = cases(json.loads(a.data.read_bytes()), embed.Encoder(), a.n, a.seed, skip)
        cs_file.write_text(json.dumps(cs))
    for name in a.arms.split(","):
        summary = run_arm(name, make_arm(name, a.jev_key_file), cs, a.out)
        (a.out / f"{name}.summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
