"""Recurrence-based consolidation with decay, measured against drawing every episode.

    python -m bench.recur patterns [-n RUNS] [NAME ...]   # bench/patterns: habits kept, one-offs not
    python -m bench.extract crowd --build recur            # LongMemEval in one crowded mind
    python -m bench.follow --build recur                   # the same, through a real agent

After RecMem (Dai et al., arXiv 2605.16045): every exchange is embedded as it's kept, with no model
call, and a model is only asked to draw facts once something recurs. RecMem counts close neighbours
against a fixed threshold; here each neighbour counts for less the longer ago it happened,
exp(-age / TAU), so five requests in a week cross the threshold and five in a year don't.

When an episode ends, each of its exchanges is scored against every exchange kept before it. The
exchanges that cross the threshold, with the not-yet-drawn neighbours that put them over it, go to
the drawer in one call, dated, so a fact can state what recurs. An episode where nothing recurs
costs no call, and what it said fades with it. This is a benchmark prototype: the map says every
episode is drawn before it goes (docs/primitives/EPISODE.md), and replacing that is the owner's call.
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

import numpy as np

from tapestry import consolidate, judge as judges
from tapestry.episode import Episode
from tapestry.mind import Mind

ROOT = Path(__file__).resolve().parent
PATTERNS = ROOT / "patterns"

SIM = 0.3           # the least cosine for an earlier exchange to count as the same thing again
TAU = 7 * 86400     # seconds for a neighbour's weight to fall to 1/e
THETA = 3.0         # the score, counting the exchange itself as 1, that sends it to be drawn
CONTEXT = 6         # the most neighbours sent beside each exchange that crossed

ADDENDUM = """\
These turns were gathered from one or more conversations because they keep coming back to the \
same things. Each turn is marked with when it was said and which conversation it was in. Besides \
what the turns say, a fact can state what recurs across them when that would be worth knowing \
later, such as how often or when something happens ("the user asks for a pull request review \
most working days"); give it as inferred and cite every turn it rests on. Something that \
happened once, or several times in one sitting, is not a habit."""


class RecurDrawer(judges.ClaudeDrawer):
    prompt = "draw_recur_v1"
    system = judges._DRAW_SYSTEM + "\n\n" + ADDENDUM


@dataclass
class Units:
    """Every exchange kept so far, across episodes: what's scored against, and whether it's been drawn."""
    vecs: list[np.ndarray] = field(default_factory=list)
    ts: list[float] = field(default_factory=list)
    episode: list[str] = field(default_factory=list)
    turns: list[list[dict]] = field(default_factory=list)
    drawn: list[bool] = field(default_factory=list)


def _exchanges(turns: list[dict]) -> list[list[dict]]:
    """A user turn and the agent's replies to it."""
    out: list[list[dict]] = []
    for t in turns:
        if t["source"] == "user" or not out:
            out.append([t])
        else:
            out[-1].append(t)
    return out


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def draw_recurrent(mind: Mind, ep: Episode, drawer, units: Units, *, sim: float = SIM, tau: float = TAU,
                   theta: float = THETA) -> dict:
    """Score `ep`'s exchanges against everything kept before them; draw facts from those that recur."""
    stats = {"exchanges": 0, "crossed": 0, "calls": 0, "cost_usd": 0.0, "facts": 0, "confirmed": 0,
             "replaced": 0, "dropped": 0}
    new = _exchanges(ep.turns(after=ep.drawn_through))
    if not new:
        return stats
    texts = [next((t["text"] for t in x if t["source"] == "user"), x[0]["text"])[:2000] for x in new]
    vecs = mind._encode(texts)
    chosen: dict[int, None] = {}
    for x, v in zip(new, vecs):
        i = len(units.vecs)
        t = x[0]["ts"]
        if units.vecs:
            cos = np.vstack(units.vecs) @ v
            near = [j for j in np.argsort(-cos) if cos[j] >= sim]
            score = 1.0 + sum(np.exp(-(t - units.ts[j]) / tau) for j in near)
        else:
            near, score = [], 1.0
        units.vecs.append(v); units.ts.append(t); units.episode.append(ep.id)
        units.turns.append(x); units.drawn.append(False)
        stats["exchanges"] += 1
        if score >= theta:
            stats["crossed"] += 1
            chosen[i] = None
            for j in [j for j in near if not units.drawn[j]][:CONTEXT]:
                chosen[j] = None
    if chosen:
        order = sorted(chosen, key=lambda j: units.ts[j])
        labels = {e: n for n, e in enumerate(dict.fromkeys(units.episode[j] for j in order), 1)}
        window = [{**t, "seq": n, "text": f"[{_when(t['ts'])}, conversation {labels[units.episode[j]]}] {t['text']}"}
                  for n, (j, t) in enumerate(((j, t) for j in order for t in units.turns[j]), 1)]
        every = [t["name"] for t in mind.topics()]
        known_ids: list[int] = []
        for j in order:
            if units.episode[j] == ep.id:
                known_ids += [r.id for r in mind.recall(texts[new.index(units.turns[j])][:1000], topics=every, k=3)]
        known = [(m, mind.content(m)) for m in list(dict.fromkeys(known_ids))[:consolidate.KNOWN]]
        for part in consolidate.windows(window):
            answer = drawer.draw(place=ep.place, date="", topics=mind.topics(), known=known,
                                 turns=[(n, t["source"], consolidate._shown(t["text"])) for n, t in enumerate(part, 1)])
            stats["calls"] += 1
            stats["cost_usd"] += drawer.last.get("cost_usd") or 0.0
            described = {}
            for nt in answer.get("new_topics") or []:
                try:
                    described[consolidate.topic_name(nt["name"])] = str(nt.get("description") or "").strip()
                except (KeyError, TypeError, ValueError):
                    continue
            existing, born = {t["name"] for t in mind.topics()}, []
            for fact in answer.get("facts") or []:
                done = consolidate._record(mind, ep, drawer, part, fact, {m for m, _ in known}, existing, described, born)
                stats["dropped" if done is None else done[0]] += 1
        for j in order:
            units.drawn[j] = True
    ep.mark_drawn(new[-1][-1]["seq"])
    return stats


# -- pattern fixtures -----------------------------------------------------------

@dataclass
class Series:
    name: str
    description: str
    episodes: list[tuple[float, list[tuple[str, str]]]]
    expect: list[dict]
    must_not: list[str]


def load(path: Path) -> Series:
    raw = tomllib.loads(path.read_text())
    episodes = []
    for e in raw["episode"]:
        at = dt.datetime.fromisoformat(e["date"]).replace(tzinfo=dt.timezone.utc).timestamp()
        turns = []
        for t in e["turn"]:
            turns += [("user", t["user"])] + ([("agent", t["agent"])] if t.get("agent") else [])
        episodes.append((at, turns))
    return Series(raw["name"], raw.get("description", ""), sorted(episodes, key=lambda x: x[0]),
                  raw.get("expect", []), raw.get("must_not", []))


def draw_series(s: Series, build: str, drawer) -> dict:
    """Every episode of `s` through one way of consolidating, in a fresh mind."""
    from tapestry import embed
    with tempfile.TemporaryDirectory(prefix="tapestry-recur-") as tmp:
        mind = Mind(Path(tmp) / "mind.db", encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
        units, calls, cost = Units(), 0, 0.0
        try:
            for n, (at, turns) in enumerate(s.episodes):
                ep = mind.episode(f"{s.name}-{n}")
                for i, (who, text) in enumerate(turns):
                    ep.add_turn(f"{s.name}-{n}:{i}", who, text, at + 60 * i)
                ep.end(at + 60 * len(turns))
                if build == "recur":
                    st = draw_recurrent(mind, ep, drawer, units)
                    calls, cost = calls + st["calls"], cost + st["cost_usd"]
                else:
                    st = consolidate.draw_episode(mind, ep, drawer)
                    calls, cost = calls + st["windows"], cost + (drawer.last.get("cost_usd") or 0.0)
            facts = [{"id": m, "content": c, "said_by": mind.evidence(m)[0]["source"], "topics": ["general"],
                      "replaces": []}
                     for m, c in mind.db.execute("SELECT id, content FROM memories WHERE superseded_by IS NULL ORDER BY id")]
        finally:
            mind.close()
    return {"facts": facts, "superseded": [], "calls": calls, "cost_usd": cost}


def cmd_patterns(names: list[str], builds: list[str], model: str, judge_model: str, runs: int, log: bool) -> int:
    from bench import extract
    series = [load(p) for p in sorted(PATTERNS.glob("*.toml")) if not names or p.stem in names]
    rows = []
    for s in series:
        flat = [(who, f"({_when(at + 60 * i)}) {text}") for at, turns in s.episodes for i, (who, text) in enumerate(turns)]
        fx = extract.Fixture(name=s.name, description=s.description, turns=flat, expect=s.expect, must_not=s.must_not,
                             date=f"{_when(s.episodes[0][0])[:10]} to {_when(s.episodes[-1][0])[:10]}")
        for build in builds:
            for r in range(runs):
                drawer = RecurDrawer(model) if build == "recur" else judges.ClaudeDrawer(model)
                got = draw_series(s, build, drawer)
                sc = extract.score(fx, got, extract.judge(fx, got, judge_model))
                rows.append({"series": s.name, "build": build, "run": r, "calls": got["calls"],
                             "cost_usd": round(got["cost_usd"], 4), **{k: sc.get(k) for k in
                             ("facts", "expected", "found", "missed", "forbidden", "unsupported", "error")}})
                print(f"\n## {s.name} [{build}] {got['calls']} calls, {len(got['facts'])} facts")
                for f in got["facts"]:
                    print(f"   - [{f['said_by']}] {f['content']}")
                for k in ("missed", "forbidden", "error"):
                    if sc.get(k):
                        print(f"   {k}: {sc[k]}")
    summary = {}
    for build in builds:
        g = [r for r in rows if r["build"] == build and not r["error"]]
        exp = sum(r["expected"] or 0 for r in g)
        summary[build] = {"found": round(sum(r["found"] or 0 for r in g) / exp, 2) if exp else None,
                          "forbidden": sum(len(r["forbidden"] or []) for r in g),
                          "facts": sum(r["facts"] for r in g), "calls": sum(r["calls"] for r in g),
                          "cost_usd": round(sum(r["cost_usd"] for r in g), 3),
                          "errors": sum(1 for r in rows if r["build"] == build and r["error"])}
    print("\n" + json.dumps(summary, indent=1))
    if log:
        with open(extract.RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "patterns", "drawer": model, "judge_model": judge_model,
                                 "params": {"sim": SIM, "tau_days": TAU / 86400, "theta": THETA},
                                 "metrics": summary, "rows": rows}) + "\n")
    return 1 if any(v["errors"] for v in summary.values()) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.recur", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="haiku", help="the drawer's model")
    ap.add_argument("--judge-model", default="sonnet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("patterns")
    p.add_argument("names", nargs="*")
    p.add_argument("--builds", nargs="+", default=["extract", "recur"], choices=["extract", "recur"])
    p.add_argument("-n", "--runs", type=int, default=1)
    p.add_argument("--log", action="store_true", help="append the result to bench/results.jsonl")
    a = ap.parse_args(argv)
    return cmd_patterns(a.names, a.builds, a.model, a.judge_model, a.runs, a.log)


if __name__ == "__main__":
    sys.exit(main())
