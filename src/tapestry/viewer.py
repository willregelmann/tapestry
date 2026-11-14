"""The viewer: where an owner looks at a mind, outside any conversation (docs/interfaces/VIEWER.md).

A surface, so it only translates. Everything it shows is read through a Mind opened with
Mind.reading, which the store itself keeps from writing, and is labelled with the same
functions that label what the agent is given. It serves one page and a little JSON, on
this machine's loopback address only, to whoever holds the address it printed.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from tapestry import belief, episode, jobs, recall, render, unrecorded
from tapestry.mind import GENERAL, Mind, RecallFailed, Recalled

# A deliberate lookup in the viewer finds as many as the agent's own tool may ask for.
LOOKUP = 20

# The page can reach nothing but this server, so nothing it shows leaves the machine.
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Viewer:
    """What the viewer shows, as plain data. One mind, read one request at a time."""

    def __init__(self, mind: Mind, *, notices: Callable[[Mind], list[str]] = lambda m: [],
                 name: str = "") -> None:
        self.mind, self._notices, self.name = mind, notices, name or mind.path.stem
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            topics = [{"name": GENERAL, "description": "What matters in every context. Always searched.",
                       "memories": self.mind.general_size()}, *self.mind.topics()]
            return {"mind": self.name, "path": str(self.mind.path), "topics": topics,
                    "notices": self._notices(self.mind)}

    def memories(self, topics: list[str], *, superseded: bool = False) -> dict:
        """The memories in these topics and where their associations lead, as of now."""
        with self._lock:
            now = time.time()
            shown = self.mind.memories(topics, superseded=superseded)
            ids = {m["id"] for m in shown}
            out, links, elsewhere = [], [], set()
            for m in shown:
                out.append({**self._labelled(m, now), "conflicts": self.mind.conflicts(m["id"])})
                for dst, s in self.mind.associations(m["id"], now).items():
                    if dst in ids:
                        links.append({"from": m["id"], "to": dst, "strength": round(s, 3)})
                    else:
                        elsewhere.add(dst)
            return {"memories": out, "associations": links, "leading_elsewhere": len(elsewhere)}

    def memory(self, memory_id: int) -> dict | None:
        """Everything about one memory, or None when there is no such memory. A forgotten one
        says only that it was forgotten."""
        with self._lock:
            if self.mind.was_forgotten(memory_id):
                return {"id": memory_id, "forgotten": True}
            try:
                m = self.mind.memory(memory_id)
            except KeyError:
                return None
            now = time.time()
            return {**self._labelled(m, now),
                    "evidence": [{**e, "when": render.learned(e["ts"]), "ago": belief.ago(e["ts"], now)}
                                 for e in self.mind.evidence(memory_id)],
                    "supersedes": [self._brief(i, now) for i in self.mind.supersedes(memory_id)],
                    "superseded_by": self._brief(m["superseded_by"], now) if m["superseded_by"] else None,
                    "conflicts": [self._brief(i, now) for i in self.mind.conflicts(memory_id)],
                    "leads_to": self._led(self.mind.associations(memory_id, now), now),
                    "led_from": self._led(self.mind.associations_into(memory_id, now), now),
                    "came_from": [{"source": w["source"], "text": w["text"], "when": render.learned(w["ts"])}
                                  for w in episode.words_of(self.mind, memory_id)]}

    def recall(self, moment: str, topics: list[str]) -> dict:
        """What the agent would have been given before a turn, word for word, and what a
        deliberate lookup with its tool would find, over the general topic and `topics`."""
        searched = [t for t in dict.fromkeys(topics) if t != GENERAL]
        with self._lock:
            try:
                auto = recall.before_turn(self.mind, moment, searched)
                found = self.mind.recall(moment, topics=searched, k=LOOKUP)
            except RecallFailed as e:
                return {"error": f"recall couldn't search this mind ({e}); nothing was searched"}
            return {"searched": [GENERAL, *searched],
                    "as_given": render.automatic(auto),
                    "highlights": [self._recalled(r) for r in auto.highlights],
                    "more": [{"topic": t or GENERAL, "memories": n} for t, n in auto.more.items()],
                    "unopened": auto.topics,
                    "found": [self._recalled(r) for r in found]}

    # -- labels: the same words the agent is given ------------------------------

    def _labelled(self, m: dict, now: float) -> dict:
        b = self.mind.belief(m["id"], now)
        return {"id": m["id"], "content": m["content"], "topics": m["topics"] or [GENERAL],
                "learned": render.learned(m["created_at"]), "created_at": m["created_at"],
                "superseded_by": m["superseded_by"], "volatility": m["volatility"],
                "belief": _belief(b, now)}

    def _brief(self, memory_id: int, now: float) -> dict:
        m = self.mind.memory(memory_id)
        return {"id": memory_id, "content": m["content"], "topics": m["topics"] or [GENERAL],
                "superseded_by": m["superseded_by"], "belief": _belief(self.mind.belief(memory_id, now), now)}

    def _led(self, strengths: dict[int, float], now: float) -> list[dict]:
        return [{**self._brief(i, now), "strength": round(s, 3)}
                for i, s in sorted(strengths.items(), key=lambda kv: -kv[1])]

    @staticmethod
    def _recalled(r: Recalled) -> dict:
        return {"id": r.id, "content": r.content, "topics": list(r.topics) or [GENERAL],
                "match": None if r.brought_by is not None else r.match,
                "brought_by": r.brought_by, "cosine": round(r.cosine, 3), "learned": render.learned(r.created_at),
                "belief": _belief(r.belief, time.time()), "conflicts": list(r.conflicts),
                "line": render.line(r)}


def _belief(b: belief.Belief | None, now: float) -> dict | None:
    if b is None:
        return None
    return {"word": b.word, "p": round(min(b.p, 0.99), 2), "why": belief.why(b, now)}


def notices(behind: unrecorded.Ledger, gaps: unrecorded.Ledger, home: Path) -> Callable[[Mind], list[str]]:
    """What the next session will be told about work that is behind, read without telling it,
    so the session is still told."""
    def read(mind: Mind) -> list[str]:
        return [n for n in (unrecorded.notice(gaps.peek()), jobs.behind_notice(behind.peek()),
                            jobs.waiting_notice(mind, home)) if n]
    return read


# -- serving --------------------------------------------------------------------

def page() -> bytes:
    return resources.files("tapestry").joinpath("viewer.html").read_bytes()


def serve(viewer: Viewer, *, port: int = 0, token: str | None = None) -> ThreadingHTTPServer:
    """A server for `viewer` on this machine's loopback address, not yet started. Every request
    has to carry `token`, so no other page or person on the machine can read the mind."""
    token = token or secrets.token_urlsafe(24)
    body = page()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            server.last_used = time.monotonic()
            url = urlsplit(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            given = self.headers.get("X-Tapestry-Token") or q.get("t") or ""
            if host not in ("127.0.0.1", "localhost") or not hmac.compare_digest(given, token):
                return self._send(HTTPStatus.FORBIDDEN, {"error": "this viewer needs the address it printed"})
            try:
                if url.path == "/":
                    return self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                if url.path == "/api/status":
                    return self._send(HTTPStatus.OK, viewer.status())
                if url.path == "/api/memories":
                    return self._send(HTTPStatus.OK, viewer.memories(
                        _names(q.get("topics")), superseded=q.get("superseded") == "1"))
                if url.path.startswith("/api/memory/"):
                    ref = url.path.removeprefix("/api/memory/")
                    found = viewer.memory(int(ref)) if ref.isdigit() else None
                    return self._send(HTTPStatus.OK if found else HTTPStatus.NOT_FOUND,
                                      found or {"error": f"no memory #{ref}"})
                if url.path == "/api/recall":
                    moment = (q.get("q") or "").strip()
                    if not moment:
                        return self._send(HTTPStatus.BAD_REQUEST, {"error": "no moment given, so nothing was searched"})
                    return self._send(HTTPStatus.OK, viewer.recall(moment, _names(q.get("topics"))))
                return self._send(HTTPStatus.NOT_FOUND, {"error": "nothing here"})
            except Exception as e:
                return self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                                  {"error": f"the mind couldn't be read ({type(e).__name__}: {e})"})

        def _send(self, status: HTTPStatus, payload, kind: str = "application/json") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", _CSP)
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.token = token
    server.last_used = time.monotonic()
    server.url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    return server


def stop_when_unused(server: ThreadingHTTPServer, idle: float, *, every: float = 15.0) -> threading.Thread:
    """Shut `server` down once nothing has asked it anything for `idle` seconds, so a viewer
    started in the background never outlives its use."""
    def watch() -> None:
        while time.monotonic() - server.last_used < idle:
            time.sleep(min(every, idle))
        server.shutdown()
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    return t


def _names(raw: str | None) -> list[str]:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


class LazyEncoder:
    """Loads the embedding model only when the owner first tries recall."""

    def __init__(self, load: Callable[[], Callable]) -> None:
        self._load, self._encode = load, None

    def __call__(self, texts):
        if self._encode is None:
            self._encode = self._load()
        return self._encode(texts)
