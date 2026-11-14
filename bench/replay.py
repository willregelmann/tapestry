"""Own-session replay: a copy of a real mind's conversations drawn under each candidate drawer.

    python -m bench.replay [--mind DB] [-n CONVERSATIONS] [--arms haiku haiku/4 sonnet] --out DIR [--log]

Held out (PLAN.md, Refinement): nothing is tuned on it, it only confirms what the fixtures
suggest. The owner's mind is never written: each arm draws into its own copy, made with SQLite's
backup. The conversations are a seeded sample of those waiting to be drawn, with at least
MIN_TURNS turns. What was drawn goes to --out, outside the repository, for the owner to read;
the log gets counts only, never content.

Arms are a model, optionally with a budget of a fact per that many turns: `haiku`, `haiku/4`,
`sonnet`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sqlite3
import sys
from pathlib import Path

from bench.extract import RESULTS
from tapestry import consolidate, embed, judge as judges
from tapestry.episode import Episode, waiting
from tapestry.mind import Mind

MIN_TURNS = 10
SEED = 13


def _arm(name: str) -> judges.ClaudeDrawer:
    model, _, per = name.partition("/")
    return judges.ClaudeDrawer(model, turns_per_fact=int(per) if per else None)


class Counted:
    """A drawer that adds up what every call cost, since an episode can take several."""

    def __init__(self, drawer: judges.ClaudeDrawer) -> None:
        self.drawer, self.cost, self.calls = drawer, 0.0, 0
        self.prompt, self.model = drawer.prompt, drawer.model

    def draw(self, **kw) -> dict:
        out = self.drawer.draw(**kw)
        self.cost += (getattr(self.drawer, "last", {}) or {}).get("cost_usd") or 0.0
        self.calls += 1
        return out


def _copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    with sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True) as a, sqlite3.connect(dst) as b:
        a.backup(b)


def pick(mind: Mind, n: int) -> list[str]:
    eps = [e.id for e in waiting(mind) if len(e.turns()) >= MIN_TURNS]
    random.Random(SEED).shuffle(eps)
    return sorted(eps[:n])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.replay", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind", type=Path, default=None, help="the mind to copy; the Claude Code user's by default")
    ap.add_argument("-n", type=int, default=5, help="conversations to draw")
    ap.add_argument("--arms", nargs="+", default=["haiku", "haiku/4", "sonnet"])
    ap.add_argument("--out", type=Path, required=True, help="where the copies and what was drawn go; never the repo")
    ap.add_argument("--log", action="store_true", help="append counts, never content, to bench/results.jsonl")
    args = ap.parse_args(argv)
    if args.mind is None:
        from tapestry.hosts import claude_code as cc
        args.mind = cc.mind_path()
    args.out.mkdir(parents=True, exist_ok=True)
    enc = embed.Encoder()
    result: dict = {"arms": {}}
    for arm in args.arms:
        path = args.out / f"{arm.replace('/', '-')}.db"
        _copy(args.mind, path)
        mind = Mind(path, encode=enc, model_tag=embed.MODEL_TAG)
        drawer, rows = Counted(_arm(arm)), []
        try:
            chosen = pick(mind, args.n)
            before = mind.db.execute("SELECT coalesce(max(id), 0) FROM memories").fetchone()[0]
            turns = 0
            for eid in chosen:
                place = mind.db.execute("SELECT place FROM episodes WHERE id=?", (eid,)).fetchone()[0]
                ep = Episode(mind, eid, place)
                turns += len(ep.turns())
                consolidate.draw_episode(mind, ep, drawer)
            for mid, content in mind.db.execute("SELECT id, content FROM memories WHERE id > ? ORDER BY id", (before,)):
                ev = mind.evidence(mid)
                rows.append({"id": mid, "conversation": ev[0]["episode"], "said_by": ev[0]["source"],
                             "topics": sorted(e["note"] for e in ev if e["kind"] == "file"), "content": content})
        finally:
            mind.close()
        (args.out / f"{arm.replace('/', '-')}.json").write_text(json.dumps(rows, indent=1))
        by = lambda who: sum(r["said_by"] == who for r in rows)
        result["arms"][arm] = {"conversations": len(chosen), "turns": turns, "facts": len(rows),
                               "per_turn": round(len(rows) / turns, 3) if turns else None,
                               "agent_said": by("agent"), "user_said": by("user"), "calls": drawer.calls, "cost_usd": round(drawer.cost, 4),
                               "prompt": drawer.prompt}
        print(arm, json.dumps(result["arms"][arm]), flush=True)
    if args.log:
        with open(RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "replay", **result}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
