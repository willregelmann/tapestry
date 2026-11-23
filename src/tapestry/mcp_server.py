"""A minimal MCP server (stdio, JSON-RPC 2.0) exposing the agent's tools.

Dependency-free on purpose: the protocol surface needed here is four methods.
The mind is opened lazily on the first tool call, so a missing model or a
broken mind is reported through the tool result, where the agent can see it,
instead of as a server that silently failed to start.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from tapestry import tools

PROTOCOL = "2025-06-18"


class Remote:
    """The tools of a mind on a Tapestry server: each call is sent there, with the session it
    belongs to, and the server runs it with the same code a mind on this machine would."""

    def __init__(self, server: dict, session: str | None, place: str | None) -> None:
        self.server, self.session, self.place = server, session, place

    def call(self, name: str, args: dict) -> str:
        from tapestry import remote
        try:
            return remote.call(self.server, "tool", {"session": self.session, "place": self.place,
                                                     "name": name, "arguments": args})["text"]
        except remote.Unreachable as e:
            return json.dumps({"error": f"{e}; nothing was searched or saved"})


class Server:
    def __init__(self, cwd: str | None = None, instance: str | None = None) -> None:
        self.cwd = cwd or os.getcwd()
        # Claude Code starts this server itself, so the process above it is the one its hooks name.
        self.instance = instance or str(os.getppid())
        self._session: tools.Session | None = None
        self._sid: str | None = None

    def _tools(self):
        from tapestry.hosts import claude_code as cc
        # The id in the environment is the one this server was started with. Clearing or resuming
        # the conversation begins another session without restarting it, and the hooks say which.
        sid = cc.session_of(self.instance) or os.environ.get("CLAUDE_CODE_SESSION_ID")
        srv = cc.server()
        if srv:
            return Remote(srv, sid, cc.project_topic(self.cwd))
        if self._session is None or sid != self._sid:
            mind = self._session.mind if self._session else cc.open_mind(self.cwd)
            place = cc.project_topic(self.cwd)
            self._sid = sid
            self._session = tools.Session(
                mind, cc.open_topics(self.cwd, sid), place=place,
                episode=mind.episode(sid, place) if sid else None,
                on_change=lambda names: cc.save_open_topics(self.cwd, sid, names))
        return self._session

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notifications need no reply
            return None
        if method == "initialize":
            result: Any = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "tapestry", "version": "0.0.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": t["name"], "description": t["description"],
                                 "inputSchema": t["parameters"]} for t in tools.SCHEMAS]}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                text = self._tools().call(p.get("name", ""), p.get("arguments") or {})
                is_error = "error" in json.loads(text)
            except Exception as e:
                text = json.dumps({"error": f"tapestry isn't available ({type(e).__name__}: {e}); "
                                            "nothing was searched or saved"})
                is_error = True
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    server = Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = server.handle(json.loads(line))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
