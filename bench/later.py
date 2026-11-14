"""Later prompts: what a later session is shown, before a prompt and when it looks (docs/capabilities/RECALL.md).

    python -m bench.later [--mind ideal|drawn] [-v] [--log]

One crowded mind: the LongMemEval mind from `bench.extract crowd`, whose facts and topics are
the distractors, with every extraction fixture written into it in date order. Two ways in:

- ideal: each fixture's expected facts written straight in, filed where the fixture says (the
  general topic when it doesn't), and linked to each other as facts drawn from one moment are. No model is asked, so a change to recall is measured with drawing held still.
- drawn: each fixture drawn by the configured drawer, as in production, then judged once to find
  which drawn memories state which expected fact. Saved under bench/data/later, so a change to
  recall never pays for drawing again.

Each fixture's [[later]] prompts then go through recall, read-only. A prompt either wants some
of its fixture's expected facts in front of the agent, or wants nothing at all.

Before the prompt, the automatic path, in arms that differ in what is searched and in what
counts as a clear match:
- boundary: the general topic and the later session's place, as built, with an unopened topic
  hinted when one of its memories matches the moment; described: the same, with topics hinted
  by their name and description instead, as first built; everything: every topic.
- fixed: a highlight clears MATCH_OK, as built; z3 and z4: a highlight stands out from what this
  moment scores against everything searched, by 3 or 4 standard deviations above its mean, and
  clears a floor.
Scored on: wanted facts highlighted; wanted facts a hint leads to, where following it with a
lookup of the topics it names returns them; highlights from outside the fixture's subject; for
prompts that want nothing, how often anything is highlighted; and topics hinted per prompt.

When the agent looks, the lookup path, with the topics that hold the wanted facts named, as an
agent following a good hint would. Arms differ in how associations carry recall outward: as
built, none, fan (a memory's pull is divided among the links leaving it, past FAN), converge
(memories reached from several results rank above those reached from one), and both. Scored on
prompts that want more than one fact, where associations can matter: all of them returned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import json
import sqlite3
import sys
import types
from pathlib import Path

import numpy as np

from bench import extract
from tapestry import consolidate, judge as judges, mind as mind_mod, recall
from tapestry.mind import Mind

DATA = extract.ROOT / "data" / "later"
LME_TYPES = ["single-session-user", "knowledge-update", "multi-session"]
LME_PER_TYPE = 10
Z_FLOOR = 0.30
DESCRIBED_FLOOR = 0.15
FAN = 2
LOOKUP_K = 5

# Fixed from the first run of each mind, before any change they judge (PLAN.md, Refinement).
# The arm as built is boundary/fixed; a ceiling for highlights nobody wanted, a floor for the rest.
THRESHOLDS: dict[str, dict[str, float]] = {
    "ideal": {"boundary/fixed:led_to": 0.65, "boundary/fixed:unwanted_per_prompt": 0.15,
              "boundary/fixed:unrelated_any": 0.15},
    "drawn": {"boundary/fixed:led_to": 0.6, "boundary/fixed:unwanted_per_prompt": 0.35,
              "boundary/fixed:unrelated_any": 0.15},
}


@functools.cache
def _encoder():
    from tapestry import embed
    return embed.Encoder()


def _base() -> Path:
    """The crowded LongMemEval mind, drawn once and cached by bench.extract."""
    drawer = extract._drawer("extract", "haiku", None)
    path, _ = extract._crowd_mind(extract._pick(LME_TYPES, LME_PER_TYPE), drawer, redraw=False)
    return path


def _copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)


def _filed(e: dict) -> list[str]:
    """Where an expected fact is filed: where the fixture says, or the general topic when it
    doesn't say, as a fixture that doesn't check filing leaves it open."""
    return [t for t in e.get("topics", []) if t != "general"]


def _start(mind: Mind, fx: extract.Fixture) -> dict[str, int]:
    for t in fx.topics:
        mind.topic(t["name"], t.get("description", ""))
    return {k["id"]: mind.remember(k["content"], source=k.get("said_by", "user"), topics=k.get("topics", []),
                                   at=extract._ts(fx.date) - 86400 * 30, key=f"{fx.name}:known:{k['id']}")
            for k in fx.known}


def build_ideal(fixtures: list[extract.Fixture], path: Path) -> dict:
    """Expected facts written straight in. Returns, per fixture, the memory ids of each expected fact."""
    _copy(_base(), path)
    mind = Mind(path, encode=_encoder(), model_tag=_encoder().tag)
    wants: dict[str, dict[str, list[int]]] = {}
    try:
        for fx in sorted(fixtures, key=lambda f: f.date):
            known, written = _start(mind, fx), []
            wants[fx.name] = {}
            for e in fx.expect:
                mid = mind.remember(e["fact"], source=extract.SAID.get(e.get("said_by", "user"), "user"),
                                    topics=_filed(e), at=extract._ts(fx.date, len(fx.turns)),
                                    key=f"{fx.name}:expect:{e['fact']}", associate=written,
                                    supersedes=known.get(e.get("replaces")) if e.get("replaces") else None)
                written.append(mid)
                if "id" in e:
                    wants[fx.name][e["id"]] = [mid]
    finally:
        mind.close()
    return {"wants": wants}


def build_drawn(fixtures: list[extract.Fixture], path: Path, drawer, judge_model: str) -> dict:
    """Every fixture drawn as in production, then judged for which drawn memory states which
    expected fact. A judge that errors twice fails the build: nothing is guessed."""
    _copy(_base(), path)
    mind = Mind(path, encode=_encoder(), model_tag=_encoder().tag)
    wants: dict[str, dict[str, list[int]]] = {}
    cost, drawn = 0.0, {}
    try:
        for fx in sorted(fixtures, key=lambda f: f.date):
            known = _start(mind, fx)
            before = mind.db.execute("SELECT coalesce(max(id), 0) FROM memories").fetchone()[0]
            ep = mind.episode(fx.name, fx.place)
            for i, (who, text) in enumerate(fx.turns):
                ep.add_turn(f"{fx.name}:{i}", who, text, extract._ts(fx.date, i))
            ep.add_recalled([known[r] for r in fx.recalled], extract._ts(fx.date))
            ep.end(extract._ts(fx.date, len(fx.turns)))
            consolidate.draw_episode(mind, ep, drawer)
            cost += getattr(drawer, "last", {}).get("cost_usd") or 0.0
            facts = [{"id": mid, "content": c, "said_by": mind.evidence(mid)[0]["source"]}
                     for mid, c in mind.db.execute("SELECT id, content FROM memories WHERE id > ? ORDER BY id", (before,))]
            drawn[fx.name] = len(facts)
            print(f"drew {fx.name}: {len(facts)} facts", flush=True)
            if not fx.expect:
                continue
            for _ in range(2):
                verdict = extract.judge(fx, {"facts": facts}, judge_model)
                if not verdict.get("error"):
                    break
            else:
                raise RuntimeError(f"{fx.name}: the judge failed twice: {verdict['error']}")
            cost += verdict.get("judge_cost_usd") or 0.0
            letters = {chr(65 + i): e.get("id") for i, e in enumerate(fx.expect)}
            found: dict[str, set[int]] = {}
            for letter, n in verdict["expected"].items():
                if n and 0 < n <= len(facts):
                    found.setdefault(letter, set()).add(facts[n - 1]["id"])
            for n, d in verdict["drawn"].items():
                for letter in letters:
                    if letter in (d.get("states") or "") and 0 < n <= len(facts):
                        found.setdefault(letter, set()).add(facts[n - 1]["id"])
            wants[fx.name] = {letters[l]: sorted(ids) for l, ids in found.items() if letters.get(l)}
    finally:
        mind.close()
    return {"wants": wants, "drawn": drawn, "cost_usd": round(cost, 4), "drawer": drawer.model,
            "prompt": drawer.prompt}


def _mind(kind: str, fixtures: list[extract.Fixture], drawer_model: str, judge_model: str, rebuild: bool,
          turns_per_fact: int | None = None) -> tuple[Mind, dict]:
    """The crowded mind for `kind`, built once per fixture set (and drawer), then reused."""
    drawer = judges.ClaudeDrawer(drawer_model, turns_per_fact=turns_per_fact) if kind == "drawn" else None
    material = [[f.name, f.date, f.place, f.turns, f.topics, f.known, f.recalled, f.expect] for f in fixtures]
    key = hashlib.sha256(json.dumps([kind, material, _base().name, drawer and [drawer.model, drawer.effort, drawer.system, *([drawer.prompt] if turns_per_fact else [])],
                                     judge_model if drawer else None], default=str).encode()).hexdigest()[:12]
    DATA.mkdir(parents=True, exist_ok=True)
    path, manifest_path = DATA / f"{kind}-{key}.db", DATA / f"{kind}-{key}.json"
    if rebuild or not (path.exists() and manifest_path.exists()):
        partial = path.with_suffix(".partial")
        manifest = build_drawn(fixtures, partial, drawer, judge_model) if drawer else build_ideal(fixtures, partial)
        partial.rename(path)
        manifest_path.write_text(json.dumps(manifest, indent=1))
    return Mind.reading(path, encode=_encoder(), model_tag=_encoder().tag), json.loads(manifest_path.read_text())


class Pool:
    """Every current memory's vector and topics, so an arm can say how a moment scores against
    everything it searches without asking recall to rank all of it."""

    def __init__(self, mind: Mind) -> None:
        rows = mind.db.execute("SELECT id, embedding FROM memories WHERE superseded_by IS NULL").fetchall()
        self.ids = [r[0] for r in rows]
        self.M = np.vstack([np.frombuffer(r[1], dtype="<f4") for r in rows])
        topics: dict[int, set[str]] = {i: set() for i in self.ids}
        for mid, name in mind.db.execute("SELECT x.memory_id, t.name FROM memory_topics x JOIN topics t ON t.id = x.topic_id"):
            if mid in topics:
                topics[mid].add(name)
        self.topics = [topics[i] for i in self.ids]

    def cosines(self, qv: np.ndarray, searched: list[str]) -> np.ndarray:
        visible = np.array([not t or bool(t & set(searched)) for t in self.topics])
        return (self.M @ qv)[visible]


def _clear(hits: list, bar: str, pool_cos: np.ndarray) -> list:
    direct = [h for h in hits if h.brought_by is None]
    if bar == "fixed":
        return [h for h in direct if h.match == "ok"][:recall.HIGHLIGHTS]
    z = float(bar[1:])
    cut = max(Z_FLOOR, float(pool_cos.mean() + z * pool_cos.std()))
    return [h for h in direct if h.cosine >= cut][:recall.HIGHLIGHTS]


def _described(mind: Mind, prompt: str, opened: list[str]) -> list[str]:
    """Topic hints as they were first built, for comparison: unopened topics whose name and
    description, never their memories, match the moment at DESCRIBED_FLOOR or more."""
    closed = [t for t in mind.topics() if t["name"] not in opened]
    if not closed:
        return []
    vecs = mind._encode([prompt] + [f"{t['name']}: {t['description'] or t['name']}" for t in closed])
    ranked = sorted(zip(vecs[1:] @ vecs[0], (t["name"] for t in closed)), reverse=True)
    return [name for c, name in ranked[:recall.TOPIC_HINTS] if c >= DESCRIBED_FLOOR]


def automatic(mind: Mind, pool: Pool, prompt: str, place: str | None, wants: dict[str, set[int]],
              subject: set[int], searching: str, bar: str) -> dict:
    """One prompt through the automatic path in one arm, scored."""
    every = [t["name"] for t in mind.topics()]
    searched = every if searching == "everything" else ([place] if place and place in every else [])
    qv = mind._encode([prompt])[0]
    hits = mind.recall(prompt, topics=searched, k=recall.DEPTH)
    highlights = _clear(hits, bar, pool.cosines(qv, searched))
    auto = recall._automatic(mind, prompt, searched, hits, highlights)
    if searching == "described":
        auto = recall.Automatic(auto.highlights, auto.more, _described(mind, prompt, searched))
    shown = {h.id for h in highlights}
    hinted = bool(auto.more or auto.topics)
    followed = {h.id for h in mind.recall(prompt, topics=[*searched, *auto.topics], k=LOOKUP_K)} if hinted else set()
    return {"wanted": len(wants),
            "shown": sum(1 for ids in wants.values() if ids & shown),
            "led_to": sum(1 for ids in wants.values() if ids & (shown | followed)),
            "unwanted": len(shown - subject), "highlights": len(shown), "topic_hints": len(auto.topics),
            "seen": [h.content[:90] for h in highlights if h.id not in subject]}


def _edges(mind: Mind, found: list[int], seen: set[int], now: float):
    inflow: dict[int, float] = {}
    out_degree: dict[int, int] = {}
    edges = []
    for src, dst, s, used in mind.db.execute(
            f"SELECT src, dst, strength, last_used_at FROM associations"
            f" WHERE dst IN (SELECT dst FROM associations WHERE src IN ({','.join('?' * len(found))}))"
            f" OR src IN ({','.join('?' * len(found))})", [*found, *found]):
        if src not in seen or dst not in seen:
            continue
        s = mind_mod._faded(s, used, now)
        inflow[dst] = inflow.get(dst, 0.0) + s
        out_degree[src] = out_degree.get(src, 0) + 1
        if src in found and dst not in found:
            edges.append((src, dst, s))
    return [(src, dst, s * min(1.0, mind_mod.INFLOW_CAP / inflow[dst]), out_degree[src]) for src, dst, s in edges]


def _spreading(fan: bool, converge: bool):
    """A replacement for Mind._brought_to_mind: the same edges, read with the fan effect, converging
    cues, or both. Neither changes which memories a lookup can see."""
    def brought(self: Mind, found: list[int], by_id: dict, now: float, *, limit: int) -> dict:
        if not found:
            return {}
        reached: dict[int, tuple[float, str, int]] = {}
        for src, dst, s, degree in _edges(self, found, set(by_id), now):
            if fan:
                s *= min(1.0, FAN / degree)
            if converge and dst in reached:
                reached[dst] = (reached[dst][0] + s, "association", reached[dst][2])
            elif dst not in reached or reached[dst][0] < s:
                reached[dst] = (s, "association", src)
        kept = {d: r for d, r in reached.items() if r[0] >= mind_mod.FOLLOW}
        return dict(sorted(kept.items(), key=lambda kv: -kv[1][0])[:limit])
    return brought


SPREADING = {"as built": None, "none": lambda self, found, by_id, now, *, limit: {},
             "fan": _spreading(True, False), "converge": _spreading(False, True), "fan+converge": _spreading(True, True)}


def lookup(mind: Mind, prompt: str, place: str | None, wants: dict[str, set[int]], arm: str) -> dict:
    """The agent looks, naming the topics that hold what's wanted."""
    names = {r[0] for ids in wants.values() for mid in ids for r in mind.db.execute(
            "SELECT t.name FROM memory_topics x JOIN topics t ON t.id = x.topic_id WHERE x.memory_id = ?", (mid,))}
    searched = sorted(names | ({place} if place else set()))
    if SPREADING[arm] is not None:
        mind._brought_to_mind = types.MethodType(SPREADING[arm], mind)
    try:
        got = {h.id for h in mind.recall(prompt, topics=searched, k=LOOKUP_K)}
    finally:
        mind.__dict__.pop("_brought_to_mind", None)
    return {"wanted": len(wants), "found": sum(1 for ids in wants.values() if ids & got)}


def run(mind: Mind, manifest: dict, fixtures: list[extract.Fixture], verbose: bool) -> dict:
    pool = Pool(mind)
    prompts = []
    for fx in fixtures:
        have = {k: set(v) for k, v in manifest["wants"].get(fx.name, {}).items()}
        # What this fixture taught: any of its expected facts, wanted by this prompt or not, is on
        # the subject; anything else highlighted (another conversation's facts, narration) is unwanted.
        subject = set().union(*have.values()) if have else set()
        for later in fx.later:
            place = later.get("place", fx.place) or None
            prompts.append((fx.name, later["prompt"], place, {w: have[w] for w in later["wants"] if w in have},
                            len(later["wants"]), len([w for w in later["wants"] if w not in have]), subject))
    out: dict = {"prompts": len(prompts), "unrelated": sum(1 for p in prompts if not p[4]),
                 "wanted_missing": sum(p[5] for p in prompts), "automatic": {}, "lookup": {}}
    for searching in ("boundary", "described", "everything"):
        for bar in ("fixed", "z3", "z4"):
            rows = [(p, automatic(mind, pool, p[1], p[2], p[3], p[6], searching, bar)) for p in prompts]
            want = [(p, r) for p, r in rows if p[4]]
            none = [r for p, r in rows if not p[4]]
            rate = lambda key, rs: round(sum(r[key] for _, r in rs) / (sum(r["wanted"] for _, r in rs) or 1), 3)
            placed = [(p, r) for p, r in want if p[2]]
            unplaced = [(p, r) for p, r in want if not p[2]]
            out["automatic"][f"{searching}/{bar}"] = {
                "shown": rate("shown", want), "led_to": rate("led_to", want),
                "shown_placed": rate("shown", placed), "led_to_placed": rate("led_to", placed),
                "shown_unplaced": rate("shown", unplaced), "led_to_unplaced": rate("led_to", unplaced),
                "unwanted_per_prompt": round(sum(r["unwanted"] for _, r in want) / len(want), 2),
                "unrelated_any": round(sum(r["highlights"] > 0 for r in none) / len(none), 3) if none else None,
                "topic_hints_per_prompt": round(sum(r["topic_hints"] for _, r in rows) / len(rows), 2)}
            if verbose:
                print(f"\n== {searching}/{bar}")
                for p, r in rows:
                    print(f"  [{p[0]}] {p[1][:70]!r}: shown {r['shown']}/{r['wanted']}, led to {r['led_to']}"
                          + "".join(f"\n      unwanted: {s}" for s in r["seen"]))
    multi = [p for p in prompts if len(p[3]) > 1]
    for arm in SPREADING:
        rows = [lookup(mind, p[1], p[2], p[3], arm) for p in multi]
        out["lookup"][arm] = {"all_found": round(sum(r["found"] == r["wanted"] for r in rows) / len(rows), 3),
                              "found": round(sum(r["found"] for r in rows) / sum(r["wanted"] for r in rows), 3),
                              "prompts": len(rows)}
    return out


def failures(kind: str, result: dict) -> list[str]:
    out = []
    for name, limit in THRESHOLDS.get(kind, {}).items():
        arm, metric = name.rsplit(":", 1)
        v = result["automatic"].get(arm, {}).get(metric)
        ceiling = metric.startswith("unwanted") or metric == "unrelated_any"
        if v is not None and ((ceiling and v > limit) or (not ceiling and v < limit)):
            out.append(f"{name} {v} vs {limit}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.later", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind", choices=["ideal", "drawn"], default="ideal")
    ap.add_argument("--drawer-model", default="haiku")
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--turns-per-fact", type=int, default=None, help="draw with a budget of a fact per this many turns")
    ap.add_argument("--rebuild", action="store_true", help="build the mind again even if one is saved")
    ap.add_argument("-v", "--verbose", action="store_true", help="every prompt, with what was highlighted unwanted")
    ap.add_argument("--log", action="store_true", help="append the result to bench/results.jsonl")
    args = ap.parse_args(argv)
    fixtures = [f for f in extract.load_all() if f.later or f.expect]
    mind, manifest = _mind(args.mind, fixtures, args.drawer_model, args.judge_model, args.rebuild, args.turns_per_fact)
    try:
        result = {"mind": args.mind, **run(mind, manifest, fixtures, args.verbose)}
    finally:
        mind.close()
    if args.mind == "drawn":
        result.update(drawer=manifest.get("drawer"), prompt=manifest.get("prompt"), drawn=sum(manifest.get("drawn", {}).values()))
    print(json.dumps(result, indent=1))
    if args.log:
        with open(extract.RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "later", **result}) + "\n")
    bad = failures(args.mind, result)
    for b in bad:
        print("FAIL", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
