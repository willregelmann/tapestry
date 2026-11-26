"""The learning session's tool server for bench.study: tapestry's own tools, plus, in the read
arm, tapestry_read, which reads a source the agent chose into memory after the session.

    TAPESTRY_HOME=... TAPESTRY_OWNER=... STUDY_TOOLS=a,b,c STUDY_QUEUE=path python -m bench.study_mcp

A prototype of the design in PLAN.md (Learning from sources): the agent judges what to read;
Tapestry does the reading. The address is checked and its passages counted now, so the agent
hears at once when a source can't be read; drawing waits for the session to end, as drawing
from a conversation does.
"""

from __future__ import annotations

import json
import os
import sys

from bench import sources
from tapestry import mcp_server, tools

READ = {
    "name": "tapestry_read",
    "description": "Read a source into your long-term memory: a web page, a wiki article, an arXiv "
                   "paper or a PDF, by its address. After this session it is read passage by passage "
                   "and what it says that is worth knowing later is kept, each fact citing the source, "
                   "so you don't need to note what it says yourself. Use it for a source worth "
                   "learning from in full; you choose what to read, and a source off the subject "
                   "costs a place without teaching anything. It returns at once with the source's "
                   "title and how many passages will be read, or why it can't be read. Nothing is "
                   "in memory until the session ends, so recall won't find it yet.",
    "parameters": {"type": "object", "required": ["url"], "properties": {
        "url": {"type": "string", "description": "The source's address."},
        "topic": {"type": "string", "description": "Where to file what it teaches. Omit for this "
                  "session's topic."}}}}


class StudyServer(mcp_server.Server):
    def __init__(self) -> None:
        super().__init__()
        self.allowed = [t for t in os.environ.get("STUDY_TOOLS", "").split(",") if t]
        self.queue = os.environ.get("STUDY_QUEUE")

    def _schemas(self) -> list[dict]:
        return [t for t in [*tools.SCHEMAS, READ] if t["name"] in self.allowed]

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": t["name"], "description": t["description"], "inputSchema": t["parameters"]}
                for t in self._schemas()]}}
        if method == "tools/call":
            p = msg.get("params") or {}
            name = p.get("name", "")
            if name not in self.allowed:
                text = json.dumps({"error": f"unknown tool {name}"})
            elif name == "tapestry_read":
                text = json.dumps(self._read(p.get("arguments") or {}))
            else:
                return super().handle(msg)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": "error" in json.loads(text)}}
        return super().handle(msg)

    def _read(self, args: dict) -> dict:
        url = str(args.get("url") or "")
        try:
            page = sources.read(url)
        except sources.Unreadable as e:
            return {"error": f"{e}; nothing will be read"}
        with open(self.queue, "a") as fh:
            fh.write(json.dumps({"url": url, "topic": args.get("topic") or None}) + "\n")
        return {"queued": page["title"], "url": page["url"], "passages": len(page["sections"]),
                "note": "Read into memory after this session ends."}


def main() -> int:
    server = StudyServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = server.handle(json.loads(line))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
