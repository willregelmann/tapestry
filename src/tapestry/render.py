"""How recalled memories are shown to an agent, the same in every host.
How the agent should read them is in tapestry.guide.

Match and belief are separate labels with separate vocabularies, so neither
can be read as the other (docs/capabilities/RECALL.md). Match is how well a
memory fits the moment; belief is how much it is believed, and why.
"""

from __future__ import annotations

import datetime as dt
import time

from tapestry import belief
from tapestry.mind import Recalled
from tapestry.recall import Automatic

# The most characters of recall put in front of the agent before a turn, in every host.
PROMPT_BUDGET = 9_000


def learned(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%b %Y")


def line(r: Recalled, *, with_id: bool = True) -> str:
    ident = f"#{r.id} · " if with_id else ""
    topics = f"{', '.join(r.topics)} · " if r.topics else ""
    b = r.belief
    belief_label = (f"belief: {b.word} ({min(b.p, 0.99):.2f}), {belief.why(b, time.time())}" if b
                    else "belief: unknown")
    conflict = f" · CONFLICTS with {', '.join(f'#{c}' for c in r.conflicts)}" if r.conflicts else ""
    # Reached through an association, not matched to the moment, and it says which memory brought it.
    match = (f"brought to mind by #{r.brought_by}" if with_id else "brought to mind by another memory") \
        if r.brought_by is not None else f"match: {r.match}"
    return (f"[{ident}{topics}{match} · cos {r.cosine:.2f} · {belief_label}{conflict}"
            f" · learned {learned(r.created_at)}] {r.content}")


def block(results: list[Recalled], *, with_id: bool = True) -> str:
    return "\n".join(line(r, with_id=with_id) for r in results)


def budgeted(results: list[Recalled], budget: int, *, via_tool: bool = True) -> str:
    """block() cut to `budget` characters in rank order, never mid-memory except the first.

    Results are kept from the best match down until one doesn't fit; that one and every
    weaker one are left out, so a weaker memory is never shown in place of a stronger one
    (docs/capabilities/RECALL.md). If the best match alone is over budget it is cut and
    says so, rather than leaving the block empty. A line at the end counts what was left out.
    `via_tool` says whether the reader can fetch the rest with tapestry_recall; a tool's own
    output can't point at itself.
    """
    whole = "; tapestry_recall shows it whole" if via_tool else ""
    rest = "tapestry_recall shows them" if via_tool else "a narrower query shows them"
    shown, used = [], 0
    for i, r in enumerate(results):
        text = line(r)
        cost = len(text) + (1 if shown else 0)
        if used + cost <= budget:
            shown.append(text); used += cost
        elif not shown:
            note = f" [cut: {len(r.content)} chars{whole}]"
            shown.append(text[:max(0, budget - len(note))] + note)
            left = len(results) - i - 1
            break
        else:
            left = len(results) - i
            break
    else:
        left = 0
    if left:
        shown.append(f"({left} more {'memory' if left == 1 else 'memories'} matched but "
                     f"didn't fit; {rest})")
    return "\n".join(shown)


def automatic(a: Automatic, budget: int = PROMPT_BUDGET) -> str:
    """Highlights in full, then hints at where more may be, or nothing when there's neither."""
    hints = []
    if a.more:
        where = ", ".join(t or "the general topic" for t in a.more)
        n = sum(a.more.values())
        hints.append(f"{n} more {'memory matches' if n == 1 else 'memories match'} in {where}")
    if a.topics:
        hints.append(f"unopened topics that may bear on this: {', '.join(a.topics)}")
    hint = (f"Hints: {'; '.join(hints)}. If they could matter, look with tapestry_recall, "
            "naming any topic that isn't open.") if hints else ""
    body = budgeted(a.highlights, budget - len(hint) - 1) if a.highlights else ""
    return "\n".join(p for p in (body, hint) if p)


def directory(topics: list[dict], open_: list[str]) -> str:
    """The topic list the agent can always see: names and descriptions, never contents."""
    if not topics:
        return ""
    rows = "\n".join(f"- {t['name']}{' (open)' if t['name'] in open_ else ''}: "
                      f"{t['description'] or 'no description'} ({t['memories']} memories)"
                      for t in topics)
    return ("\n\nMemory topics. Only the general topic and open ones are searched "
            "automatically; name others in tapestry_recall to search them too:\n" + rows)
