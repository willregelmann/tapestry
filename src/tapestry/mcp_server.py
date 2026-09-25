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


class Server:
    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd or os.getcwd()
        self._session: tools.Session | None = None

    def _tools(self) -> tools.Session:
        if self._session is None:
            from tapestry.hosts import claude_code as cc
            mind = cc.open_mind(self.cwd)
            scopes = cc.loaded_scopes(self.cwd)
            self._session = tools.Session(
                mind, scopes, home_scope=scopes[1] if len(scopes) > 1 else scopes[0],
                on_scopes_changed=lambda s: cc.save_extra_scopes(self.cwd, s))
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
