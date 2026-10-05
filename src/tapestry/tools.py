"""The agent's tools, identical in every host (invariants/interfaces/AGENT.md).

Hosts expose these however they expose tools (Hermes provider tools, an MCP
server for Claude Code and the Agent SDK) and pass calls through here. Every
result is JSON. Failures say what did and didn't happen, so an error can never
be read as "memory holds nothing".
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tapestry import render
from tapestry.mind import Mind, RecallFailed

_NAMESPACES_ARG = {"type": "array", "items": {"type": "string"},
                   "description": "Namespaces to search alongside the default and the "
                                  "session's open ones, e.g. [\"finance\"]. See tapestry_namespaces."}

SCHEMAS = [
    {"name": "tapestry_recall",
     "description": "Search your long-term memory on purpose, beyond what was recalled "
                    "automatically. Searches the default namespace, the namespaces open in "
                    "this session, and any you name. Results carry the same labels.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to recall, in plain language."},
         "namespaces": _NAMESPACES_ARG,
         "limit": {"type": "integer", "default": 5, "description": "Maximum memories."}},
         "required": ["query"]}},
    {"name": "tapestry_why",
     "description": "Show the evidence behind a memory: who said it, when, which namespaces "
                    "it was filed under, and what has superseded it since.",
     "parameters": {"type": "object", "properties": {
         "memory": {"type": "integer", "description": "The #number of a recalled memory."}},
         "required": ["memory"]}},
    {"name": "tapestry_note",
     "description": "Remember something deliberately: a fact, decision or preference worth "
                    "keeping. Write it as a standalone statement. File it under namespaces "
                    "when it only matters in some contexts; leave them out when it matters "
                    "everywhere.",
     "parameters": {"type": "object", "properties": {
         "content": {"type": "string", "description": "The memory, as a standalone statement."},
         "namespaces": {**_NAMESPACES_ARG, "description":
                        "Where to file it, e.g. [\"will\", \"finance\"]. Omit to use the "
                        "session's open namespaces; pass [] for the default namespace only."},
         "source": {"type": "string", "enum": ["user", "agent", "tool", "web"],
                    "default": "user",
                    "description": "Who it came from: the user said it, you concluded it, "
                                   "or a tool or the web reported it."},
         "supersedes": {"type": "integer", "description":
                        "The #number of a recalled memory this note corrects or replaces. "
                        "That memory stops being recalled; tapestry_why still shows it. "
                        "Saying 'this replaces...' in the text doesn't do this."}},
         "required": ["content"]}},
    {"name": "tapestry_namespaces",
     "description": "List every namespace with its description (never its contents), open "
                    "or close one for the rest of this session, or create one and describe it.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "open", "close", "describe"],
                    "default": "list"},
         "namespace": {"type": "string", "description": "Namespace name, for open, close or describe."},
         "description": {"type": "string", "description": "For describe: what the namespace "
                         "holds, in one line, so it can be found without opening it."}}}},
]


class Session:
    """What a tool call needs: the mind, the session's open namespaces, and how
    to show text to this host."""

    def __init__(self, mind: Mind, open_namespaces: list[str], *,
                 on_change: Callable[[list[str]], None] | None = None,
                 escape: Callable[[str], str] = lambda s: s) -> None:
        self.mind = mind
        self.open = open_namespaces
        self._changed = on_change
        self._escape = escape

    def call(self, name: str, args: dict[str, Any] | None) -> str:
        args = args or {}
        try:
            return json.dumps(self._call(name, args))
        except RecallFailed as e:
            return json.dumps({"error": f"recall failed ({e}); no memories were searched"})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}; nothing was changed"})

    def _unknown(self, names: list[str]) -> list[str]:
        known = {n["name"] for n in self.mind.namespaces()}
        return [n for n in names if n not in known]

    def _call(self, name: str, args: dict[str, Any]) -> dict:
        if name == "tapestry_recall":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return {"error": "no query received, so no search ran"}
            extra = list(args.get("namespaces") or [])
            missing = self._unknown(extra)
            if missing:
                return {"error": f"no namespace {', '.join(missing)}; nothing was searched. "
                                 "tapestry_namespaces lists the ones that exist."}
            searched = list(dict.fromkeys(self.open + extra))
            hits = self.mind.recall(query, namespaces=searched, k=int(args.get("limit") or 5))
            result = {"searched": ["default", *searched]}
            if not hits:
                return {**result, "memories": "", "note": "searched; nothing related found"}
            return {**result, "memories": self._escape(render.block(hits))}
        if name == "tapestry_why":
            mid = int(args["memory"])
            ev = self.mind.evidence(mid)
            if not ev:
                return {"error": f"no memory #{mid}"}
            return {"memory": mid, "evidence": [{**e, "when": render.learned(e["ts"])} for e in ev]}
        if name == "tapestry_note":
            content = args.get("content")
            if not isinstance(content, str) or not content.strip():
                return {"error": "empty note; nothing saved"}
            names = self.open if args.get("namespaces") is None else list(args["namespaces"])
            old = args.get("supersedes")
            if old is not None:
                if isinstance(old, str):
                    old = old.strip().removeprefix("#")   # the description says "#number"
                if isinstance(old, bool) or not (isinstance(old, int) or
                                                 (isinstance(old, str) and old.isdigit())):
                    return {"error": f"supersedes must be a memory's #number, got {old!r}; "
                                     "nothing saved"}
                old = int(old)
                # Visibility first, and an unknown id gets the same answer as a hidden one:
                # otherwise the refusal tells a session which ids exist in namespaces it can't
                # see (and whether they've been superseded).
                if not self.mind.visible(old, list(dict.fromkeys(self.open + names))):
                    return {"error": f"no memory #{old} this session can see; if it's filed in "
                                     "another namespace, open that or file this note there. "
                                     "Nothing saved."}
                by = self.mind.superseded_by(old)
                if by is not None:
                    return {"error": f"#{old} was already superseded by #{by}; supersede #{by} "
                                     "instead if it's the one that's wrong. Nothing saved."}
            mid = self.mind.remember(content.strip(), source=args.get("source") or "user",
                                     namespaces=names, supersedes=old)
            out = {"saved": mid, "namespaces": names or ["default"]}
            return {**out, "supersedes": old} if old is not None else out
        if name == "tapestry_namespaces":
            action, ns = args.get("action") or "list", (args.get("namespace") or "").strip()
            if action != "list" and not ns:
                return {"error": f"{action} needs a namespace name"}
            if action == "describe":
                if not (args.get("description") or "").strip():
                    return {"error": "describe needs a description"}
                self.mind.namespace(ns, args["description"])
            elif action == "open":
                if self._unknown([ns]):
                    return {"error": f"no namespace {ns!r}; describe it first to create it"}
                if ns not in self.open:
                    self.open.append(ns)
            elif action == "close":
                self.open[:] = [n for n in self.open if n != ns]
            if action in ("open", "close") and self._changed:
                self._changed(self.open)
            return {"namespaces": self.mind.namespaces(), "open": self.open,
                    "note": "The default namespace is always searched."}
        return {"error": f"unknown tool {name}"}
