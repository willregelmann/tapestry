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
from tapestry.mind import USER_SCOPE, Mind, RecallFailed

SCHEMAS = [
    {"name": "tapestry_recall",
     "description": "Search your long-term memory on purpose, beyond what was recalled "
                    "automatically. Results carry the same match labels.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to recall, in plain language."},
         "limit": {"type": "integer", "default": 5, "description": "Maximum memories."}},
         "required": ["query"]}},
    {"name": "tapestry_why",
     "description": "Show the evidence behind a memory: who said it, when, and what has "
                    "confirmed or superseded it since.",
     "parameters": {"type": "object", "properties": {
         "memory": {"type": "integer", "description": "The #number of a recalled memory."}},
         "required": ["memory"]}},
    {"name": "tapestry_note",
     "description": "Remember something deliberately: a fact, decision or preference worth "
                    "keeping. Write it as a standalone statement.",
     "parameters": {"type": "object", "properties": {
         "content": {"type": "string", "description": "The memory, as a standalone statement."},
         "source": {"type": "string", "enum": ["user", "agent", "tool", "web"],
                    "default": "user",
                    "description": "Who it came from: the user said it, you concluded it, "
                                   "or a tool or the web reported it."}},
         "required": ["content"]}},
    {"name": "tapestry_scopes",
     "description": "List your memory's scopes and which are loaded, or load or unload one "
                    "for this session.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "load", "unload"], "default": "list"},
         "scope": {"type": "string", "description": "Scope name, for load or unload."}}}},
]


class Session:
    """What a tool call needs: the mind, the loaded scopes, and how to show text."""

    def __init__(self, mind: Mind, scopes: list[str], *, home_scope: str | None = None,
                 on_scopes_changed: Callable[[list[str]], None] | None = None,
                 escape: Callable[[str], str] = lambda s: s) -> None:
        self.mind = mind
        self.scopes = scopes
        self.home_scope = home_scope or scopes[-1]
        self._changed = on_scopes_changed
        self._escape = escape

    def call(self, name: str, args: dict[str, Any] | None) -> str:
        args = args or {}
        try:
            return json.dumps(self._call(name, args))
        except RecallFailed as e:
            return json.dumps({"error": f"recall failed ({e}); no memories were searched"})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}; nothing was changed"})

    def _call(self, name: str, args: dict[str, Any]) -> dict:
        if name == "tapestry_recall":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return {"error": "no query received, so no search ran"}
            hits = self.mind.recall(query, scopes=self.scopes, k=int(args.get("limit") or 5))
            if not hits:
                return {"memories": "", "note": "searched; nothing related found"}
            return {"memories": self._escape(render.block(hits))}
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
            mid = self.mind.remember(content.strip(), source=args.get("source") or "user",
                                     scope=self.home_scope)
            return {"saved": mid, "scope": self.home_scope}
        if name == "tapestry_scopes":
            action, scope = args.get("action") or "list", args.get("scope")
            if action in ("load", "unload") and not scope:
                return {"error": f"{action} needs a scope name"}
            known = self.mind.scopes()
            if action == "load":
                if scope not in known:
                    return {"error": f"no scope {scope!r}", "scopes": known}
                if scope not in self.scopes:
                    self.scopes.append(scope)
            elif action == "unload":
                if scope == USER_SCOPE:
                    return {"error": "the user-wide scope is always loaded"}
                self.scopes[:] = [s for s in self.scopes if s != scope]
            if action != "list" and self._changed:
                self._changed(self.scopes)
            return {"scopes": known, "loaded": self.scopes}
        return {"error": f"unknown tool {name}"}
