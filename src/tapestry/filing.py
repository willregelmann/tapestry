"""Where a turn is filed, and which session namespaces a session sees: one rule for every host.

docs/primitives/NAMESPACE.md: an observation is filed in its session's own
namespace, and in its place's namespace when the session has one; never the
default, and never in a namespace only because it's open. Adapters say what the
session and its place are (docs/interfaces/HOOKS.md) and file nothing themselves.
"""

from __future__ import annotations

import re

SESSION_PREFIX = "session."
_NS_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def session_namespace(session_id: str | None) -> str:
    """The namespace a session's own turns are filed in.

    Other sessions reach it only by naming it.
    """
    slug = _NS_UNSAFE.sub("-", (session_id or "").lower()).strip("-._") or "unknown"
    return SESSION_PREFIX + slug


def turn_namespaces(session_id: str | None, place: str | None = None) -> list[str]:
    """Where one turn is filed: its session's namespace, plus its place's when it has one."""
    own = session_namespace(session_id)
    return [own] + ([place] if place and place != own else [])


def searched(open_names: list[str], own: str | None, extra: list[str] = ()) -> list[str]:
    """What a session's lookups see besides the default: what's open, its own namespace,
    and any named for one lookup."""
    return list(dict.fromkeys([*open_names, *([own] if own else []), *extra]))


def listing(namespaces: list[dict], session_id: str | None) -> tuple[list[dict], int]:
    """The namespaces a session lists, and how many other sessions' it counts instead.

    There is one session namespace per conversation, so listing each would grow
    the agent's context without bound.
    """
    own = session_namespace(session_id)
    listed = [n for n in namespaces if not n["name"].startswith(SESSION_PREFIX) or n["name"] == own]
    return listed, len(namespaces) - len(listed)
