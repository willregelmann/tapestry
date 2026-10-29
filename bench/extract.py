"""Extraction: what Consolidate draws from a conversation, scored (docs/capabilities/CONSOLIDATE.md).

    python -m bench.extract fixtures [-n RUNS] [NAME ...]   # the hand-written conversations in bench/extraction
    python -m bench.extract lme -n 20              # LongMemEval's evidence sessions

Each conversation goes through the real path: a throwaway mind, an episode, the
configured drawer (Claude), and the same checks as in production. Then:

- What can be read off the mind is read off it: whether a conversation that taught
  nothing added nothing, who each fact is attributed to, where it was filed, and which
  memory a correction replaced.
- What takes judgment goes to an isolated judge, which sees the conversation, the
  facts drawn and the expectations: which expected fact each drawn one states, whether
  each drawn fact is supported by what was said, and whether any forbidden claim
  was drawn. A judge that errors is reported as an error, never as a pass.

A fixture (bench/extraction/*.toml):

    name = "home-correction"
    description = "..."
    host = "hermes"             # who'd have this conversation; informational
    place = "atlas"             # optional: the session's place
    date = "2026-10-19"
    nothing = true              # optional: nothing lasting was said
    recalled = ["trash"]        # optional: known memories in mind during it
    must_not = ["..."]          # optional: claims that must not be drawn
    [[topic]]   name, description
    [[known]]   id, content, topics, said_by       # memories the mind already holds
    [[turn]]    user, agent
    [[expect]]  fact, said_by?, topics?, replaces?  # "general" is the general topic

The run fails (exit 1) when it crosses THRESHOLDS. LongMemEval has no expected
facts, only the turns that hold each answer, so it's scored by whether a drawn
fact cites each such turn, and by whether the current memories answer the question.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from bench.scenario import _claude
from tapestry import consolidate, judge as judges
from tapestry.mind import Mind

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "extraction"
RESULTS = ROOT / "results.jsonl"

THRESHOLDS = {"recall": 0.8, "supported": 0.95, "forbidden": 0, "nothing": 1.0,
              "attribution": 0.9, "filing": 0.8, "replaced": 1.0}
SAID = {"user": "user", "agent": "agent", "inferred": "inference"}


@dataclass
class Fixture:
    name: str
    description: str
    date: str
    turns: list[tuple[str, str]]
    place: str | None = None
    host: str = ""
    nothing: bool = False
    topics: list[dict] = field(default_factory=list)
    known: list[dict] = field(default_factory=list)
    recalled: list[str] = field(default_factory=list)
    expect: list[dict] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)


def load(path: Path) -> Fixture:
    raw = tomllib.loads(path.read_text())
    turns = []
    for t in raw.get("turn", []):
        turns.append(("user", t["user"]))
        if t.get("agent"):
            turns.append(("agent", t["agent"]))
    fx = Fixture(name=raw["name"], description=raw.get("description", ""), date=raw["date"], turns=turns,
                 place=raw.get("place") or None, host=raw.get("host", ""), nothing=raw.get("nothing", False),
                 topics=raw.get("topic", []), known=raw.get("known", []), recalled=raw.get("recalled", []),
                 expect=raw.get("expect", []), must_not=raw.get("must_not", []))
    ids = {k["id"] for k in fx.known}
    bad = [r for r in fx.recalled if r not in ids] + [e["replaces"] for e in fx.expect
                                                      if e.get("replaces") and e["replaces"] not in ids]
    if bad or not turns or (fx.nothing and fx.expect):
        raise ValueError(f"{path.name}: unknown known ids {bad}, no turns, or both nothing and expect")
    return fx


def load_all(names: list[str] | None = None) -> list[Fixture]:
    out = [load(p) for p in sorted(FIXTURES.glob("*.toml"))]
    return [f for f in out if not names or f.name in names]


def _mind(tmp: Path) -> Mind:
    from tapestry import embed
    return Mind(tmp / "mind.db", encode=embed.Encoder(), model_tag=embed.MODEL_TAG)


def _ts(date: str, minute: int = 0) -> float:
    return dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp() + 36000 + 60 * minute


def draw(fx: Fixture, drawer) -> dict:
    """Run one conversation through Consolidate. Returns what the mind holds afterwards."""
    with tempfile.TemporaryDirectory(prefix="tapestry-extract-") as tmp:
        mind = _mind(Path(tmp))
        try:
            for t in fx.topics:
                mind.topic(t["name"], t.get("description", ""))
            known = {k["id"]: mind.remember(k["content"], source=k.get("said_by", "user"),
                                            topics=k.get("topics", []), at=_ts(fx.date) - 86400 * 30)
                     for k in fx.known}
            ep = mind.episode(fx.name, fx.place)
            for i, (who, text) in enumerate(fx.turns):
                ep.add_turn(f"{fx.name}:{i}", who, text, _ts(fx.date, i))
            ep.add_recalled([known[r] for r in fx.recalled], _ts(fx.date))
            ep.end(_ts(fx.date, len(fx.turns)))
            stats = consolidate.draw_episode(mind, ep, drawer)
            facts = []
            for mid, content in mind.db.execute("SELECT id, content FROM memories ORDER BY id"):
                if mid in known.values():
                    continue
                ev = mind.evidence(mid)
                facts.append({"id": mid, "content": content, "said_by": ev[0]["source"],
                              "topics": sorted(e["note"] for e in ev if e["kind"] == "file") or ["general"],
                              "replaces": [k for k, kid in known.items() if mind.superseded_by(kid) == mid]})
            confirmed = [k for k, kid in known.items()
                         if any(e["kind"] == "confirm" for e in mind.evidence(kid))]
            return {"facts": facts, "confirmed": confirmed, "stats": stats,
                    "drawer_cost_usd": getattr(drawer, "last", {}).get("cost_usd")}
        finally:
            mind.close()


JUDGE_PROMPT = """\
You grade what a memory system wrote down from a conversation. You see the conversation, \
the facts it drew (numbered), the facts it was expected to draw (lettered), and claims it \
must not draw. You also see what the system knew going in: where the conversation happened, \
its topics and the memories it already held, which a fact may draw on for names. Be strict and literal.
- For each expected fact, give the number of a drawn fact that states it, in substance, or 0. \
A drawn fact that states more than one expected fact can be given for each.
- For each drawn fact, say whether the conversation supports it: everything it claims was said, \
or follows directly from what was said and the date. A fact that adds a name, number, date or \
cause nobody said is unsupported, and so is a pronoun or gender nobody gave. Each drawn fact \
says who it is attributed to: "user" or "agent" means that speaker made the claim themselves, \
and "inference" means neither said it outright. A claim attributed to a speaker who only \
relayed someone else's words (a pasted email, a quote) is unsupported.
- For each forbidden claim, give the number of a drawn fact that states it or implies it, even \
with a date, a name or other detail added, or 0."""
JUDGE_SCHEMA = json.dumps({"type": "object", "required": ["expected", "drawn", "forbidden"], "properties": {
    "expected": {"type": "array", "items": {"type": "object", "required": ["letter", "drawn"], "properties": {
        "letter": {"type": "string"}, "drawn": {"type": "integer"}}}},
    "drawn": {"type": "array", "items": {"type": "object", "required": ["number", "supported", "why"], "properties": {
        "number": {"type": "integer"}, "supported": {"type": "boolean"}, "why": {"type": "string"}}}},
    "forbidden": {"type": "array", "items": {"type": "object", "required": ["index", "drawn"], "properties": {
        "index": {"type": "integer"}, "drawn": {"type": "integer"}}}}}})


def judge(fx: Fixture, got: dict, model: str) -> dict:
    letters = [chr(65 + i) for i in range(len(fx.expect))]
    convo = "\n\n".join(f"[{i}] {who}: {text}" for i, (who, text) in enumerate(fx.turns, 1))
    context = (f"Where it happened: {fx.place or 'no particular place'}\nTopics: "
               + ("; ".join(f"{t['name']} ({t.get('description', '')})" for t in fx.topics) or "none")
               + "\nAlready known: " + ("; ".join(k["content"] for k in fx.known) or "nothing"))
    prompt = (f"{context}\n\nThe conversation took place on {fx.date}.\n\n{convo}\n\nDrawn facts:\n"
              + ("\n".join(f"{n}. [{f['said_by']}] {f['content']}" for n, f in enumerate(got["facts"], 1))
                 or "(none)")
              + "\n\nExpected facts:\n" + ("\n".join(f"{l}. {e['fact']}" for l, e in zip(letters, fx.expect))
                                           or "(none)")
              + "\n\nMust not be drawn:\n" + ("\n".join(f"{i}. {m}" for i, m in enumerate(fx.must_not, 1))
                                             or "(none)"))
    if not got["facts"]:
        return {"expected": {l: 0 for l in letters}, "drawn": {}, "forbidden": {}, "error": None}
    try:
        out = json.loads(_claude(["--output-format", "json", "--json-schema", JUDGE_SCHEMA],
                                 prompt, model, JUDGE_PROMPT))
        if out.get("is_error"):
            raise RuntimeError(str(out.get("result"))[:300])
        v = out["structured_output"]
        expected = {e["letter"]: e["drawn"] for e in v["expected"]}
        drawn = {d["number"]: d for d in v["drawn"]}
        forbidden = {f["index"]: f["drawn"] for f in v["forbidden"]}
        if (set(expected) != set(letters) or set(drawn) != set(range(1, len(got["facts"]) + 1))
                or set(forbidden) != set(range(1, len(fx.must_not) + 1))):
            raise RuntimeError("the judge didn't answer for every fact")
        return {"expected": expected, "drawn": drawn, "forbidden": forbidden, "error": None,
                "judge_cost_usd": out.get("total_cost_usd")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def score(fx: Fixture, got: dict, verdict: dict) -> dict:
    """Per-fixture counts. Judged counts are None when the judge errored."""
    facts = got["facts"]
    s = {"name": fx.name, "facts": len(facts), "nothing_ok": None if not fx.nothing else
         (not facts and not any(f["replaces"] for f in facts))}
    replaces = [e for e in fx.expect if e.get("replaces")]
    s["replace_ok"] = sum(any(e["replaces"] in f["replaces"] for f in facts) for e in replaces)
    s["replace_n"] = len(replaces)
    if verdict.get("error"):
        return {**s, "error": verdict["error"]}
    letters = [chr(65 + i) for i in range(len(fx.expect))]
    matched = {l: verdict["expected"].get(l, 0) for l in letters}
    s["expected"], s["found"] = len(letters), sum(1 for n in matched.values() if n)
    s["supported"] = sum(1 for d in verdict["drawn"].values() if d["supported"])
    s["unsupported"] = [(facts[n - 1]["content"], d["why"]) for n, d in verdict["drawn"].items()
                        if not d["supported"] and 0 < n <= len(facts)]
    s["forbidden"] = [fx.must_not[i - 1] for i, n in verdict["forbidden"].items() if n and 0 < i <= len(fx.must_not)]
    attr = filed = attr_n = filed_n = 0
    for e, l in zip(fx.expect, letters):
        n = matched[l]
        if not n or not 0 < n <= len(facts):
            continue
        f = facts[n - 1]
        if e.get("said_by"):
            attr_n += 1
            attr += f["said_by"] == SAID.get(e["said_by"], e["said_by"])
        if e.get("topics"):
            filed_n += 1
            filed += set(f["topics"]) == set(e["topics"])
    s.update(attr=attr, attr_n=attr_n, filed=filed, filed_n=filed_n,
             missed=[e["fact"] for e, l in zip(fx.expect, letters) if not matched[l]])
    return s


def totals(scores: list[dict]) -> dict:
    ok = [s for s in scores if not s.get("error")]
    ratio = lambda a, b: (sum(s[a] for s in ok) / b) if b else None
    exp, drawn = sum(s["expected"] for s in ok), sum(s["facts"] for s in ok)
    nothing = [s["nothing_ok"] for s in scores if s["nothing_ok"] is not None]
    rep_n = sum(s["replace_n"] for s in scores)
    return {"fixtures": len(scores), "errors": len(scores) - len(ok),
            "recall": ratio("found", exp), "supported": ratio("supported", drawn),
            "forbidden": sum(len(s["forbidden"]) for s in ok),
            "nothing": (sum(nothing) / len(nothing)) if nothing else None,
            "attribution": ratio("attr", sum(s["attr_n"] for s in ok)),
            "filing": ratio("filed", sum(s["filed_n"] for s in ok)),
            "replaced": (sum(s["replace_ok"] for s in scores) / rep_n) if rep_n else None}


def failures(t: dict) -> list[str]:
    out = [f"{t['errors']} fixture(s) couldn't be judged"] if t["errors"] else []
    for k, limit in THRESHOLDS.items():
        v = t.get(k)
        if v is None:
            continue
        if (k == "forbidden" and v > limit) or (k != "forbidden" and v < limit):
            out.append(f"{k} {v:.2f} vs {limit}" if isinstance(v, float) else f"{k} {v} vs {limit}")
    return out


def cmd_fixtures(names: list[str], model: str, judge_model: str, log: bool, runs: int) -> int:
    """Each fixture `runs` times: the drawer varies from run to run, so the totals are rates."""
    drawer = judges.ClaudeDrawer(model)
    scores = []
    for fx in [f for f in load_all(names) for _ in range(runs)]:
        got = draw(fx, drawer)
        s = score(fx, got, judge(fx, got, judge_model))
        scores.append(s)
        print(f"\n## {fx.name} ({fx.host or 'any host'}): {s['facts']} facts drawn")
        for f in got["facts"]:
            print(f"   - [{f['said_by']} · {', '.join(f['topics'])}"
                  f"{' · replaces ' + ', '.join(f['replaces']) if f['replaces'] else ''}] {f['content']}")
        for k in ("error", "missed", "unsupported", "forbidden"):
            if s.get(k):
                print(f"   {k}: {s[k]}")
    t = totals(scores)
    print("\n" + json.dumps(t, indent=1))
    failed = failures(t)
    print("FAIL: " + "; ".join(failed) if failed else "PASS")
    if log:
        with open(RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "extraction", "drawer": model, "judge_model": judge_model, "runs": runs,
                                 "metrics": t, "fixtures": scores}) + "\n")
    return 1 if failed else 0


def cmd_lme(n: int, types: list[str], model: str, judge_model: str) -> int:
    """LongMemEval's evidence sessions, drawn in date order into one mind per question."""
    from bench import longmemeval as lme
    items = json.loads((lme.DATA_DIR / "longmemeval_oracle.json").read_text())
    items = [i for i in items if i["question_type"] in types and not i["question_id"].endswith("_abs")][:n]
    drawer = judges.ClaudeDrawer(model)
    cited = cites = correct = judged = 0
    for item in items:
        with tempfile.TemporaryDirectory(prefix="tapestry-lme-") as tmp:
            mind = _mind(Path(tmp))
            try:
                order = sorted(zip(item["haystack_dates"], item["haystack_session_ids"], item["haystack_sessions"]))
                for when, sid, session in order:
                    ep = mind.episode(sid)
                    at = lme._date(when).timestamp()
                    for i, t in enumerate(session):
                        ep.add_turn(f"{sid}:{i}", "user" if t["role"] == "user" else "agent", t["content"], at + i)
                    ep.end(at + len(session))
                    consolidate.draw_episode(mind, ep, drawer)
                answer_keys = {f"{sid}:{i}" for _, sid, s in order for i, t in enumerate(s) if t.get("has_answer")}
                drawn_keys = {r[0] for r in mind.db.execute("SELECT turn_key FROM drawn_from")}
                cites += len(answer_keys)
                cited += len(answer_keys & drawn_keys)
                current = [r[0] for r in mind.db.execute(
                    "SELECT content FROM memories WHERE superseded_by IS NULL ORDER BY created_at")]
            finally:
                mind.close()
        prompt = (f"Question (asked {item['question_date']}): {item['question']}\nCorrect answer: {item['answer']}\n\n"
                  "Memories:\n" + "\n".join(f"- {c}" for c in current))
        try:
            out = json.loads(_claude(["--output-format", "json", "--json-schema", json.dumps(
                {"type": "object", "required": ["answers"], "properties": {"answers": {"type": "boolean"}}})],
                prompt, judge_model, "Say whether these memories alone give the correct answer to the "
                                     "question. Strict: a stale or different value is false."))
            ok = out["structured_output"]["answers"]
            judged += 1
            correct += ok
        except Exception as e:
            ok = f"ERROR {type(e).__name__}: {e}"
        print(f"{item['question_id']} [{item['question_type']}] facts={len(current)} answers={ok}")
    print(json.dumps({"questions": len(items), "answer_turns_cited": cited / cites if cites else None,
                      "answered": correct / judged if judged else None, "errors": len(items) - judged}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.extract", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="haiku", help="the drawer's model")
    ap.add_argument("--judge-model", default="sonnet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fixtures")
    f.add_argument("names", nargs="*")
    f.add_argument("--log", action="store_true", help="append the result to bench/results.jsonl")
    f.add_argument("-n", "--runs", type=int, default=1, help="runs per fixture")
    l = sub.add_parser("lme")
    l.add_argument("-n", type=int, default=10)
    l.add_argument("--types", nargs="+", default=["single-session-user", "knowledge-update"])
    args = ap.parse_args(argv)
    if args.cmd == "fixtures":
        return cmd_fixtures(args.names, args.model, args.judge_model, args.log, args.runs)
    return cmd_lme(args.n, args.types, args.model, args.judge_model)


if __name__ == "__main__":
    sys.exit(main())
