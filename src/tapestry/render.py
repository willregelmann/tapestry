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

# The most characters of recall put in front of the agent before a turn, in every host.
PROMPT_BUDGET = 9_000


def learned(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%b %Y")


def line(r: Recalled, *, with_id: bool = True) -> str:
    ident = f"#{r.id} · " if with_id else ""
    ns = f"{', '.join(r.namespaces)} · " if r.namespaces else ""
    b = r.belief
    belief_label = (f"belief: {b.word} ({b.p:.2f}), {belief.why(b, time.time())}" if b
                    else "belief: unknown")
    conflict = f" · CONFLICTS with {', '.join(f'#{c}' for c in r.conflicts)}" if r.conflicts else ""
    return (f"[{ident}{ns}match: {r.match} · cos {r.cosine:.2f} · {belief_label}{conflict}"
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


def directory(namespaces: list[dict], open_: list[str], *, hidden_sessions: int = 0) -> str:
    """The namespace list the agent can always see: names and descriptions, never contents.

    Other sessions' transcript namespaces are counted, not listed: there is one
    per conversation, and a line each would grow the prompt without bound.
    """
    if not namespaces and not hidden_sessions:
        return ""
    rows = "\n".join(f"- {n['name']}{' (open)' if n['name'] in open_ else ''}: "
                      f"{n['description'] or 'no description'} ({n['memories']} memories)"
                      for n in namespaces)
    if hidden_sessions:
        rows += (f"\n- plus {hidden_sessions} other conversations' transcripts (session.*), "
                 "searched only when you name one")
    return ("\n\nMemory namespaces. Only the default namespace and open ones are searched "
            "automatically; name others in tapestry_recall to search them too:\n" + rows.lstrip("\n"))
