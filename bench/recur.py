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

The hybrid build keeps the map's rule: every episode is drawn as usual, and recurrence only adds
context. An exchange scores against earlier conversations, each counting once at its latest
similar exchange, so a busy sitting is one occurrence however many times it repeats itself. When
it crosses, the earlier exchanges that put it over are shown beside the episode, dated, so the
drawer can state a habit. Once a habit has been drawn, a later occurrence close enough to it is
evidence for it, with no model call, rather than a reason to draw it again.
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
HABIT = 2           # the fewest earlier conversations an exchange must recur with among those a habit
                    # rests on, to be another occurrence of that habit

ADDENDUM = """\
These turns were gathered from one or more conversations because they keep coming back to the \
same things. Each turn is marked with when it was said and which conversation it was in. Besides \
what the turns say, a fact can state what recurs across them when that would be worth knowing \
later, such as how often or when something happens ("the user asks for a pull request review \
most working days"); give it as inferred and cite every turn it rests on. Something that \
happened once, or several times in one sitting, is not a habit."""


HYBRID_ADDENDUM = """\
Turns marked as from an earlier conversation were read when that conversation ended, and what \
they taught is already known. They are shown because this conversation keeps coming back to the \
same things. Draw from this conversation's turns as usual, but one more occurrence of something \
that recurs is not a fact of its own: a request, or how it was handled, is not worth keeping \
because it happened again. From the turns together, draw what recurs across conversations when \
that would be worth knowing later, such as how often or when something happens ("the user asks \
for a pull request review most working days"). State it as the pattern in general terms that \
will still be true after more occasions like these, such as which days or what time of day, never \
as the dates seen so far; give it as inferred, and cite every turn it rests on. When a known memory already \
states the pattern, give it as same_as instead of stating it again. Something that happened once, \
or several times in one sitting, is not a habit."""


class RecurDrawer(judges.ClaudeDrawer):
    prompt = "draw_recur_v1"
    system = judges._DRAW_SYSTEM + "\n\n" + ADDENDUM


class HybridDrawer(judges.ClaudeDrawer):
    prompt = "draw_hybrid_v3"
    system = judges._DRAW_SYSTEM + "\n\n" + HYBRID_ADDENDUM


@dataclass
class Units:
    """Every exchange kept so far, across episodes: what's scored against, and whether it's been drawn."""
    vecs: list[np.ndarray] = field(default_factory=list)
    ts: list[float] = field(default_factory=list)
    episode: list[str] = field(default_factory=list)
    turns: list[list[dict]] = field(default_factory=list)
    drawn: list[bool] = field(default_factory=list)
    habits: dict[int, set[int]] = field(default_factory=dict)   # each habit drawn so far: the exchanges it rests on


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


def draw_hybrid(mind: Mind, ep: Episode, drawer, units: Units, *, sim: float = SIM, tau: float = TAU,
                theta: float = THETA, habit: float = HABIT) -> dict:
    """Draw every turn of `ep`, with the earlier exchanges it recurs with shown beside it, dated."""
    from tapestry import embed
    stats = {"exchanges": 0, "crossed": 0, "habit_confirms": 0, "calls": 0, "cost_usd": 0.0, "facts": 0,
             "confirmed": 0, "replaced": 0, "dropped": 0}
    new = _exchanges(ep.turns(after=ep.drawn_through))
    if not new:
        return stats
    texts = [next((t["text"] for t in x if t["source"] == "user"), x[0]["text"])[:2000] for x in new]
    vecs = mind._encode(texts)
    earlier = [j for j in range(len(units.vecs)) if units.episode[j] != ep.id]
    context: dict[int, None] = {}
    for x, v in zip(new, vecs):
        t = x[0]["ts"]
        cos = np.vstack([units.vecs[j] for j in earlier]) @ v if earlier else np.zeros(0)
        near = [earlier[k] for k in np.argsort(-cos) if cos[k] >= sim]
        latest: dict[str, float] = {}
        for j in near:
            latest[units.episode[j]] = max(latest.get(units.episode[j], 0.0), units.ts[j])
        score = 1.0 + sum(np.exp(-(t - at) / tau) for at in latest.values())
        units.vecs.append(v); units.ts.append(t); units.episode.append(ep.id)
        units.turns.append(x); units.drawn.append(True)
        stats["exchanges"] += 1
        if score < theta:
            continue
        stats["crossed"] += 1
        # Matched by the exchanges a habit rests on, not by its words: a request as short as
        # "coffee please" is far from any sentence stating a routine, and close to the requests.
        shared = {m: {units.episode[j] for j in near if j in rests}
                  for m, rests in units.habits.items() if mind.superseded_by(m) is None}
        best = max(shared, key=lambda m: len(shared[m]), default=None)
        if best is not None and len(shared[best]) >= habit:
            units.habits[best].add(len(units.vecs) - 1)
            mind.confirm(best, source="inference", at=t, episode=ep.id, reasoner=embed.MODEL_TAG,
                         drawn_from=[u["key"] for u in x])
            stats["habit_confirms"] += 1
            continue
        for j in near[:CONTEXT]:
            context[j] = None
    if not context:
        st = consolidate.draw_episode(mind, ep, drawer)
        stats["calls"] += st["windows"]
        stats["cost_usd"] += (drawer.last.get("cost_usd") or 0.0) if st["windows"] else 0.0
        for k in ("facts", "confirmed", "replaced", "dropped"):
            stats[k] += st[k]
        return stats
    order = sorted(context, key=lambda j: units.ts[j])
    labels = {e: n for n, e in enumerate(dict.fromkeys(units.episode[j] for j in order), 1)}
    first = len(units.vecs) - len(new)
    shown = ([{**u, "text": f"[{_when(u['ts'])}, earlier conversation {labels[units.episode[j]]}] {u['text']}",
               "episode": units.episode[j], "unit": j} for j in order for u in units.turns[j]]
             + [{**u, "text": f"[{_when(u['ts'])}, this conversation] {u['text']}", "episode": ep.id, "unit": first + n}
                for n, x in enumerate(new) for u in x])
    for part in consolidate.windows(shown):
        own = [u for u in part if u["episode"] == ep.id]
        live = [m for m in units.habits if mind.superseded_by(m) is None]
        known = [(m, mind.content(m)) for m in live] + [k for k in consolidate._known(mind, ep, own or part)
                                                         if k[0] not in units.habits][:consolidate.KNOWN - len(live)]
        answer = drawer.draw(place=ep.place, date="", topics=mind.topics(), known=known,
                             turns=[(n, u["source"], consolidate._shown(u["text"])) for n, u in enumerate(part, 1)])
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
            if done is None:
                stats["dropped"] += 1
                continue
            stats[done[0]] += 1
            cited = [part[i - 1] for i in fact.get("turns") or [] if isinstance(i, int) and 1 <= i <= len(part)]
            if len({u["episode"] for u in cited}) > 1 or done[1] in units.habits:
                units.habits.setdefault(done[1], set()).update(u["unit"] for u in cited)
    ep.mark_drawn(new[-1][-1]["seq"])
    return stats


def habits_in(mind: Mind) -> list[dict]:
    """Current inferences drawn from more than one conversation, with how many times each was confirmed."""
    out = []
    for mid, content in mind.db.execute("SELECT id, content FROM memories WHERE superseded_by IS NULL"):
        eps = {k.rsplit(":", 1)[0] for (k,) in mind.db.execute("SELECT turn_key FROM drawn_from WHERE memory_id=?", (mid,))}
        if len(eps) > 1 and mind.belief(mid).origin == "inference":
            confirms = sum(1 for e in mind.evidence(mid) if e["kind"] == "confirm")
            out.append({"id": mid, "content": content, "conversations": len(eps), "confirms": confirms,
                        "belief": round(mind.belief(mid).p, 2)})
    return out


BUILDS = {"recur": draw_recurrent, "hybrid": draw_hybrid}
DRAWERS = {"recur": RecurDrawer, "hybrid": HybridDrawer}


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
                if build in BUILDS:
                    st = BUILDS[build](mind, ep, drawer, units)
                    calls, cost = calls + st["calls"], cost + st["cost_usd"]
                else:
                    st = consolidate.draw_episode(mind, ep, drawer)
                    calls, cost = calls + st["windows"], cost + (drawer.last.get("cost_usd") or 0.0)
            facts = [{"id": m, "content": c, "said_by": mind.evidence(m)[0]["source"], "topics": ["general"],
                      "replaces": []}
                     for m, c in mind.db.execute("SELECT id, content FROM memories WHERE superseded_by IS NULL ORDER BY id")]
            habits = habits_in(mind)
        finally:
            mind.close()
    return {"facts": facts, "superseded": [], "calls": calls, "cost_usd": cost, "habits": habits}


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
                drawer = DRAWERS.get(build, judges.ClaudeDrawer)(model)
                got = draw_series(s, build, drawer)
                sc = extract.score(fx, got, extract.judge(fx, got, judge_model))
                rows.append({"series": s.name, "build": build, "run": r, "calls": got["calls"],
                             "cost_usd": round(got["cost_usd"], 4), "habits": len(got["habits"]),
                             "habit_confirms": sum(h["confirms"] for h in got["habits"]), **{k: sc.get(k) for k in
                             ("facts", "expected", "found", "missed", "forbidden", "unsupported", "error")}})
                print(f"\n## {s.name} [{build}] {got['calls']} calls, {len(got['facts'])} facts")
                for f in got["facts"]:
                    print(f"   - [{f['said_by']}] {f['content']}")
                for h in got["habits"]:
                    print(f"   habit #{h['id']}: {h['conversations']} conversations, {h['confirms']} confirms, "
                          f"belief {h['belief']}")
                for k in ("missed", "forbidden", "error"):
                    if sc.get(k):
                        print(f"   {k}: {sc[k]}")
    summary = {}
    for build in builds:
        g = [r for r in rows if r["build"] == build and not r["error"]]
        exp = sum(r["expected"] or 0 for r in g)
        summary[build] = {"found": round(sum(r["found"] or 0 for r in g) / exp, 2) if exp else None,
                          "forbidden": sum(len(r["forbidden"] or []) for r in g),
                          "facts": sum(r["facts"] for r in g), "habits": sum(r["habits"] for r in g),
                          "habit_confirms": sum(r["habit_confirms"] for r in g), "calls": sum(r["calls"] for r in g),
                          "cost_usd": round(sum(r["cost_usd"] for r in g), 3),
                          "errors": sum(1 for r in rows if r["build"] == build and r["error"])}
    print("\n" + json.dumps(summary, indent=1))
    if log:
        with open(extract.RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "patterns", "drawer": model, "judge_model": judge_model,
                                 "params": {"sim": SIM, "tau_days": TAU / 86400, "theta": THETA, "habit": HABIT},
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
    p.add_argument("--builds", nargs="+", default=["extract", "recur", "hybrid"], choices=["extract", *BUILDS])
    p.add_argument("-n", "--runs", type=int, default=1)
    p.add_argument("--log", action="store_true", help="append the result to bench/results.jsonl")
    a = ap.parse_args(argv)
    return cmd_patterns(a.names, a.builds, a.model, a.judge_model, a.runs, a.log)


if __name__ == "__main__":
    sys.exit(main())
