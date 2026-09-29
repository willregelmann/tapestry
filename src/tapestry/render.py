"""How recalled memories are shown to an agent, the same in every host.
How the agent should read them is in tapestry.guide.

Match and belief are separate labels with separate vocabularies, so neither
can be read as the other (invariants/capabilities/RECALL.md). Belief is
Stage 2; until then the label says when a memory was learned, which is a fact
about the memory, not a judgment about it.
"""

from __future__ import annotations

import datetime as dt

from tapestry.mind import Recalled

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
