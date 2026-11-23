"""The Tapestry server: one place that serves each of its owner's minds (PLAN.md stage 6).

Machines reach a mind here with a token, one per agent per machine, which the owner makes with
`tapestry server key add` (docs/primitives/KEY.md). The server keeps only a hash of each token.
A key names one mind, and every turn and evidence entry written through it names the key.

Every hook and every tool a session uses is a POST to /v1/<what> with the token as a bearer:

    whoami         the key's agent, machine, mind and access
    session-start  what a session starts with                       {session, place, place_description}
    before-turn    recall for a prompt                              {session, place, prompt}
    turns          add turns to the session's episode               {session, place, turns: [{key, source, text, ts}]}
    end            end the episode, then start background work      {session, place}
    tool           one agent tool call                              {session, place, name, arguments}

Each mind is used by one request at a time, so writes never contend. A read-only key's
requests run with the store refusing writes. Background work runs here, where the mind lives
(docs/interfaces/JOBS.md), one run at a time, after a session ends.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from tapestry import jobs, served, tools, unrecorded
from tapestry.mind import Mind

logger = logging.getLogger("tapestry.server")
NAME = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Registry:
    """The server's minds and keys, kept in its home as minds.json and keys.json."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._lock = threading.Lock()

    def _read(self, name: str) -> dict | list:
        try:
            return json.loads((self.home / name).read_text())
        except FileNotFoundError:
            return {} if name == "minds.json" else []

    def _write(self, name: str, value) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.home / f".{name}.tmp"
        tmp.write_text(json.dumps(value, indent=1))
        tmp.chmod(0o600)
        tmp.replace(self.home / name)

    def minds(self) -> dict[str, str]:
        return dict(self._read("minds.json"))

    def add_mind(self, name: str, path: str | None = None) -> Path:
        """A mind for the owner: a new one in the server's home, or a mind file that exists."""
        if not NAME.match(name):
            raise ValueError(f"a mind's name is lowercase letters, digits, '.', '_' or '-', not {name!r}")
        with self._lock:
            minds = self.minds()
            if name in minds:
                raise ValueError(f"there is already a mind called {name!r}")
            where = Path(path).expanduser().resolve() if path else self.home / "minds" / f"{name}.db"
            minds[name] = str(where)
            self._write("minds.json", minds)
        return where

    def keys(self) -> list[dict]:
        return list(self._read("keys.json"))

    def add_key(self, *, mind: str, agent: str, machine: str, read_only: bool = False) -> tuple[dict, str]:
        """A new key, and its token, which is shown this once and never kept."""
        if mind not in self.minds():
            raise ValueError(f"there is no mind called {mind!r}; `tapestry server mind add {mind}` makes one")
        token = "tap_" + secrets.token_urlsafe(32)
        with self._lock:
            keys = self.keys()
            key = {"id": f"k{len(keys) + 1}", "hash": _hash(token), "mind": mind, "agent": agent,
                   "machine": machine, "access": "read" if read_only else "write",
                   "created_at": time.time(), "revoked_at": None}
            self._write("keys.json", keys + [key])
        return key, token

    def revoke(self, key_id: str) -> dict:
        with self._lock:
            keys = self.keys()
            for k in keys:
                if k["id"] == key_id:
                    if not k["revoked_at"]:
                        k["revoked_at"] = time.time()
                        self._write("keys.json", keys)
                    return k
        raise KeyError(f"no key {key_id!r}")

    def key_for(self, token: str) -> dict | None:
        """The live key this token belongs to. Read from disk each time, so a revoke takes effect
        on the next request (docs/primitives/KEY.md)."""
        h = _hash(token)
        return next((k for k in self.keys() if secrets.compare_digest(k["hash"], h) and not k["revoked_at"]), None)


@dataclass
class Served:
    """One mind as this server holds it: one connection, used by one request at a time."""
    name: str
    mind: Mind
    lock: threading.Lock = field(default_factory=threading.Lock)


class Server:
    def __init__(self, home: Path, *, encode: Callable | None = None, model_tag: str | None = None,
                 background: bool = True) -> None:
        self.home = home
        self.registry = Registry(home)
        self._encode, self._model_tag = encode, model_tag
        self._minds: dict[str, Served] = {}
        self._lock = threading.Lock()
        self.background = background
        self.behind = unrecorded.Ledger(home / "state" / "behind.jsonl")
        self.open = served.OpenTopics(home / "state" / "open")
        self._jobs: threading.Thread | None = None

    def _encoder(self):
        if self._encode is None:
            from tapestry import embed
            self._encode, self._model_tag = embed.Encoder(), embed.MODEL_TAG
        return self._encode, self._model_tag

    def _open(self, name: str) -> Mind:
        path = Path(self.registry.minds()[name])
        path.parent.mkdir(parents=True, exist_ok=True)
        encode, tag = self._encoder()
        return Mind(path, encode=encode, model_tag=tag)

    def served(self, name: str) -> Served:
        with self._lock:
            if name not in self._minds:
                self._minds[name] = Served(name, self._open(name))
            return self._minds[name]

    def close(self) -> None:
        with self._lock:
            for s in self._minds.values():
                s.mind.close()
            self._minds.clear()

    # -- requests -------------------------------------------------------------

    def handle(self, token: str | None, what: str, body: dict) -> tuple[int, dict]:
        key = self.registry.key_for(token) if token else None
        if key is None:
            return 401, {"error": "this token isn't a live key on this server"}
        if what == "whoami":
            return 200, {k: key[k] for k in ("id", "mind", "agent", "machine", "access")}
        handler = getattr(self, "_" + what.replace("-", "_"), None)
        if handler is None:
            return 404, {"error": f"no such request: {what}"}
        writes = key["access"] == "write"
        if not writes and what in ("turns", "end"):
            return 403, {"error": "this key reads only; nothing was recorded"}
        s = self.served(key["mind"])
        with s.lock:
            s.mind.via = key["id"]
            if not writes:
                s.mind.db.execute("PRAGMA query_only=ON")
            try:
                return 200, handler(s, key, body, writes)
            finally:
                s.mind.via = None
                if not writes:
                    s.mind.db.execute("PRAGMA query_only=OFF")

    def _session_start(self, s: Served, key: dict, body: dict, writes: bool) -> dict:
        session, place = body.get("session"), body.get("place")
        if writes:
            served.ensure_place(s.mind, place, body.get("place_description"))
        text = served.session_start(
            s.mind, session=session, place=place, opened=self.open.get(session, place),
            attached=f"{s.name!r} on a Tapestry server, through key {key['id']}"
                     f" ({key['agent']} on {key['machine']})",
            notices=[jobs.behind_notice(self.behind.take()), jobs.waiting_notice(s.mind, self.home)],
            record=writes)
        return {"text": text}

    def _before_turn(self, s: Served, key: dict, body: dict, writes: bool) -> dict:
        session, place = body.get("session"), body.get("place")
        return {"text": served.before_turn(s.mind, session=session, place=place, prompt=body.get("prompt") or "",
                                           opened=self.open.get(session, place),
                                           notices=[jobs.behind_notice(self.behind.take())], record=writes)}

    def _turns(self, s: Served, key: dict, body: dict, writes: bool) -> dict:
        if not body.get("session"):
            raise ValueError("an episode belongs to a session, and this request names none")
        return served.add_turns(s.mind, session=body["session"], place=body.get("place"),
                                turns=list(body.get("turns") or []))

    def _end(self, s: Served, key: dict, body: dict, writes: bool) -> dict:
        served.end(s.mind, session=body["session"], place=body.get("place"))
        if self.background:
            self.start_jobs(s.name)
        return {"ended": body["session"]}

    def _tool(self, s: Served, key: dict, body: dict, writes: bool) -> dict:
        session, place = body.get("session"), body.get("place")
        sess = tools.Session(s.mind, self.open.get(session, place), place=place,
                             episode=s.mind.episode(session, place) if (session and writes) else None,
                             on_change=lambda names: self.open.save(session, place, names))
        text = sess.call(body.get("name") or "", body.get("arguments") or {})
        if not writes and '"error"' in text and "readonly" in text:
            text = json.dumps({"error": "this key reads only; nothing was changed"})
        return {"text": text}

    # -- background work --------------------------------------------------------

    def start_jobs(self, name: str) -> None:
        """One background run for this mind, unless one is already going."""
        if self._jobs and self._jobs.is_alive():
            return
        def run():
            out = jobs.run(self.home, lambda: self._open(name), self.behind)
            logger.info("background work on %s: %s", name, out)
        self._jobs = threading.Thread(target=run, name="tapestry-jobs", daemon=True)
        self._jobs.start()


def http_server(app: Server, host: str = "127.0.0.1", port: int = 8650) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.info("%s %s", self.address_string(), fmt % args)

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/v1/health":
                return self._reply(200, {"ok": True})
            self._reply(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.startswith("/v1/"):
                return self._reply(404, {"error": "not found"})
            auth = self.headers.get("Authorization") or ""
            token = auth[7:] if auth.startswith("Bearer ") else None
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                code, out = app.handle(token, self.path[4:], body)
            except Exception as e:
                logger.exception("request %s failed", self.path)
                code, out = 500, {"error": f"{type(e).__name__}: {e}; nothing was changed"}
            self._reply(code, out)

    return ThreadingHTTPServer((host, port), Handler)
