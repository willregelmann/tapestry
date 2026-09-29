"""How recalled memories are shown to an agent, the same in every host.

Match and belief are separate labels with separate vocabularies, so neither
can be read as the other (invariants/capabilities/RECALL.md). Belief is
Stage 2; until then the label says when a memory was learned, which is a fact
about the memory, not a judgment about it.
"""

from __future__ import annotations

import datetime as dt

from tapestry.mind import Recalled

GUIDE = (
    "Recalled memories arrive with labels. [match: ok] means the memory is on the "
    "topic of the message; [match: LOW] means it's only loosely related and may not "
    "answer anything; [match: keyword only] means it shares words, not meaning. "
    "Match says nothing about whether a memory is true or current. 'learned' is when "
    "you learned it; the older a memory about something that changes, the more it's "
    "worth checking before relying on it for anything that matters. Say what a memory "
    "does and doesn't establish rather than asserting it. The #number identifies a "
    "memory if you want to ask why you believe it. A name after it, like 'finance', is the "
    "namespace the memory is filed under; unlabelled memories are in the default namespace.")


def learned(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%b %Y")


def line(r: Recalled, *, with_id: bool = True) -> str:
    ident = f"#{r.id} · " if with_id else ""
    ns = f"{', '.join(r.namespaces)} · " if r.namespaces else ""
    return f"[{ident}{ns}match: {r.match} · cos {r.cosine:.2f} · learned {learned(r.created_at)}] {r.content}"


def block(results: list[Recalled], *, with_id: bool = True) -> str:
    return "\n".join(line(r, with_id=with_id) for r in results)


def directory(namespaces: list[dict], open_: list[str]) -> str:
    """The namespace list the agent can always see: names and descriptions, never contents."""
    if not namespaces:
        return ""
    rows = "\n".join(f"- {n['name']}{' (open)' if n['name'] in open_ else ''}: "
                      f"{n['description'] or 'no description'} ({n['memories']} memories)"
                      for n in namespaces)
    return ("\n\nMemory namespaces. Only the default namespace and open ones are searched "
            "automatically; name others in tapestry_recall to search them too:\n" + rows)
