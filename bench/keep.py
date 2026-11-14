"""A second pass over drawn facts: is each one worth keeping? Jev against Haiku.

    python -m bench.keep [--jev-key-file F] [--haiku-model haiku] [--log]

Drawing keeps too much from long conversations (PLAN.md, Refinement), and a longer drawing
prompt is the fix we'd rather not reach for. This measures the other one: leave the drawer
alone and ask one plain question of each fact it drew, by itself, without the conversation.

The facts and their labels come from bench/results.jsonl: every extraction run over a fixture
marked complete logs what was drawn and which expected fact each one states. A drawn fact
that states an expected fact is worth keeping; one that states none isn't. The same facts go
to each arm. Jev answers with a probability, so it is reported at several cut-offs; Haiku
answers yes or no for all of a conversation's facts in one call.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from bench.scenario import _claude
from tapestry import judge as judges

RESULTS = Path(__file__).resolve().parent / "results.jsonl"

# One question, the same words for both arms, about any kind of conversation.
QUESTION = "Would this still be worth knowing in a later conversation, weeks from now?"
CRITERIA = {"true": "It says something that lasts: about a person, a place, a thing, a decision, "
                    "a plan, or something still to be done",
            "false": "It only tells what was said or done during the conversation, or it will be "
                     "out of date within a day"}
CUTS = (0.3, 0.5, 0.7)


def labelled(path: Path = RESULTS) -> list[dict]:
    """Drawn facts from complete fixtures, each with whether it states an expected fact."""
    out, seen = [], set()
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row.get("kind") != "extraction":
            continue
        for fx in row.get("fixtures", []):
            if not fx.get("complete") or "drawn" not in fx:
                continue
            for d in fx["drawn"]:
                if (fx["name"], d["content"]) not in seen:
                    seen.add((fx["name"], d["content"]))
                    out.append({"fixture": fx["name"], "content": d["content"], "keep": bool(d["states"])})
    return out


def jev(facts: list[dict], key_file: Path) -> tuple[list[float], float]:
    judge = judges.JevJudge(key_file)
    ps, start = [], time.monotonic()
    for f in facts:
        body = {"model": judges.MODEL, "state": {"message": f["content"]},
                "questions": {"c:0": {"type": "noul", "criteria": CRITERIA, "instructions": {"question": QUESTION}}},
                "providerOptions": {"gateway": {"only": [judges.PROVIDER]}}}
        ps.append(judge._answers(body, 1)[0])
    return ps, time.monotonic() - start


def haiku(facts: list[dict], model: str) -> tuple[list[bool], float, float]:
    schema = json.dumps({"type": "object", "required": ["keep"], "properties": {
        "keep": {"type": "array", "items": {"type": "object", "required": ["number", "keep"], "properties": {
            "number": {"type": "integer"}, "keep": {"type": "boolean"}}}}}})
    system = (f"For each numbered statement, answer: {QUESTION} Yes when: {CRITERIA['true']}. "
              f"No when: {CRITERIA['false']}.")
    answers, cost, start = {}, 0.0, time.monotonic()
    for name in dict.fromkeys(f["fixture"] for f in facts):
        mine = [(i, f) for i, f in enumerate(facts) if f["fixture"] == name]
        prompt = "\n".join(f"{n}. {f['content']}" for n, (_, f) in enumerate(mine, 1))
        out = json.loads(_claude(["--output-format", "json", "--json-schema", schema], prompt, model, system))
        if out.get("is_error"):
            raise RuntimeError(str(out.get("result"))[:300])
        got = {a["number"]: a["keep"] for a in out["structured_output"]["keep"]}
        if set(got) != set(range(1, len(mine) + 1)):
            raise RuntimeError(f"{name}: the model didn't answer for every statement")
        for n, (i, _) in enumerate(mine, 1):
            answers[i] = got[n]
        cost += out.get("total_cost_usd") or 0.0
    return [answers[i] for i in range(len(facts))], time.monotonic() - start, cost


def rates(facts: list[dict], kept: list[bool]) -> dict:
    """Of the facts worth keeping, how many were kept; of the rest, how many were dropped."""
    worth = [k for f, k in zip(facts, kept) if f["keep"]]
    rest = [k for f, k in zip(facts, kept) if not f["keep"]]
    return {"kept_of_worth": round(sum(worth) / len(worth), 3) if worth else None,
            "dropped_of_rest": round(sum(not k for k in rest) / len(rest), 3) if rest else None,
            "worth": len(worth), "rest": len(rest)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.keep", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jev-key-file", type=Path)
    ap.add_argument("--haiku-model", default="haiku")
    ap.add_argument("--log", action="store_true", help="append the result to bench/results.jsonl")
    args = ap.parse_args(argv)
    facts = labelled()
    if not facts:
        print("no labelled facts yet: run `python -m bench.extract fixtures --log` over complete fixtures first")
        return 1
    result = {"facts": len(facts), "worth": sum(f["keep"] for f in facts), "arms": {}}
    kept, seconds, cost = haiku(facts, args.haiku_model)
    result["arms"][args.haiku_model] = {**rates(facts, kept), "seconds": round(seconds, 1), "cost_usd": round(cost, 4)}
    wrong = {args.haiku_model: [(f["keep"], f["content"]) for f, k in zip(facts, kept) if k != f["keep"]]}
    if args.jev_key_file:
        ps, seconds = jev(facts, args.jev_key_file)
        for cut in CUTS:
            result["arms"][f"jev@{cut}"] = {**rates(facts, [p >= cut for p in ps]), "seconds": round(seconds, 1)}
        wrong["jev@0.5"] = [(f["keep"], round(p, 2), f["content"]) for f, p in zip(facts, ps) if (p >= 0.5) != f["keep"]]
    print(json.dumps(result, indent=1))
    for arm, rows in wrong.items():
        print(f"\n{arm} disagreed with the labels on {len(rows)}:")
        for row in rows:
            print("  ", "worth keeping, dropped:" if row[0] else "not on the list, kept:", *row[1:])
    if args.log:
        with open(RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "keep", "question": QUESTION, **result}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
