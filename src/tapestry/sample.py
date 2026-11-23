"""The highlight sample: the owner rates a few highlights recalled lately (PLAN.md stage 6, measure 2).

Highlight precision is the share of highlights shown before a turn that the owner rates both
relevant to that moment and still true. Rating is measurement, not evidence: the mind is only
ever read here, and the ratings are kept beside it, never in it.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Callable

from tapestry import belief
from tapestry.mind import Mind

DAY = 86400.0


def pick(mind: Mind, n: int, *, days: float = 7.0, now: float | None = None,
         rng: random.Random | None = None) -> list[dict]:
    """Up to `n` highlights recalled in the last `days`, chosen at random, each with the words
    that were said at that moment while its episode still keeps them."""
    now = time.time() if now is None else now
    rows = mind.db.execute(
        "SELECT r.episode, r.memory_id, r.ts, m.content FROM recalled r JOIN memories m ON m.id = r.memory_id"
        " WHERE r.ts >= ?", (now - days * DAY,)).fetchall()
    chosen = (rng or random.Random()).sample(rows, min(n, len(rows)))
    out = []
    for episode, mid, ts, content in sorted(chosen, key=lambda r: r[2]):
        # The user's words nearest the moment: hosts record a turn after recalling for it, so
        # the words can be stamped just after the recall as well as before.
        said = mind.db.execute(
            "SELECT text FROM turns WHERE episode=? AND source='user' ORDER BY abs(ts - ?) LIMIT 1",
            (episode, ts)).fetchone()
        b = mind.belief(mid, now=now)
        out.append({"episode": episode, "memory": mid, "at": ts, "content": content,
                    "moment": said[0] if said else None,
                    "belief": f"{b.word}, {belief.why(b, now)}"})
    return out


def rate(items: list[dict], ask: Callable[[str], str], log: Path) -> list[dict]:
    """Ask the owner about each item, and keep each answer in `log`. An item they skip is not kept,
    and quitting (q, or the end of input) keeps what was rated so far."""
    log.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    for i, it in enumerate(items, 1):
        moment = it["moment"] or "(the words of that moment are no longer kept)"
        print(f"\n[{i}/{len(items)}] At this moment: {moment[:300]}\n"
              f"  was shown #{it['memory']}: {it['content'][:400]}\n  ({it['belief']})")
        try:
            relevant = _yes_no(ask, "  Relevant to that moment? [y/n, s to skip, q to quit] ")
            true = None if relevant is None else _yes_no(ask, "  Still true? [y/n, s to skip, q to quit] ")
        except (EOFError, KeyboardInterrupt):
            break
        if relevant is None or true is None:
            continue
        rating = {"rated_at": time.time(), "episode": it["episode"], "memory": it["memory"],
                  "recalled_at": it["at"], "relevant": relevant, "true": true}
        with open(log, "a") as fh:
            fh.write(json.dumps(rating) + "\n")
        kept.append(rating)
    return kept


def _yes_no(ask: Callable[[str], str], prompt: str) -> bool | None:
    while True:
        a = ask(prompt).strip().lower()[:1]
        if a in ("y", "n"):
            return a == "y"
        if a == "s":
            return None
        if a == "q":
            raise EOFError


def report(log: Path, *, days: float = 7.0, now: float | None = None) -> str:
    """Highlight precision over the ratings made in the last `days`."""
    now = time.time() if now is None else now
    if not log.is_file():
        return "No highlights rated yet. Run `tapestry sample` to rate some."
    rated = [r for r in map(json.loads, log.read_text().splitlines()) if r["rated_at"] >= now - days * DAY]
    if not rated:
        return f"No highlights rated in the last {days:g} days."
    good = sum(r["relevant"] and r["true"] for r in rated)
    relevant = sum(r["relevant"] for r in rated)
    true = sum(r["true"] for r in rated)
    return (f"Highlight precision, last {days:g} days: {good}/{len(rated)} relevant and still true"
            f" ({good / len(rated):.0%}); relevant {relevant}/{len(rated)}, still true {true}/{len(rated)}."
            + (" A small sample: read it as a direction, not a rate." if len(rated) < 20 else ""))
