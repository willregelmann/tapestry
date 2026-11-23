"""Reaching a mind on a Tapestry server (PLAN.md stage 6).

A machine connected to a server keeps its address and its token in `remote.json` beside its
own state, readable by its owner alone. Everything the hooks and tools would do to a mind on
this machine is sent there instead, and the server does it with the same code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


class Unreachable(Exception):
    """The server couldn't be reached, or refused this machine's token. Nothing was done."""


def config_file(home: Path) -> Path:
    return home / "remote.json"


def configured(home: Path) -> dict | None:
    """The server this machine reaches its mind on, or None when the mind is on this machine."""
    url, token = os.environ.get("TAPESTRY_SERVER"), os.environ.get("TAPESTRY_TOKEN")
    if url and token:
        return {"url": url.rstrip("/"), "token": token}
    try:
        c = json.loads(config_file(home).read_text())
        return {"url": c["url"].rstrip("/"), "token": c["token"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def connect(home: Path, url: str, token: str) -> dict:
    """Check the server knows this token, then keep both. Returns what the server says the key is."""
    who = call({"url": url.rstrip("/"), "token": token}, "whoami", {})
    f = config_file(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"url": url.rstrip("/"), "token": token}, fh)
    return who


def disconnect(home: Path) -> bool:
    try:
        config_file(home).unlink()
        return True
    except FileNotFoundError:
        return False


def call(server: dict, what: str, body: dict, *, timeout: float = 20.0) -> dict:
    """POST one request. Raises Unreachable when it couldn't be made or was refused."""
    req = urllib.request.Request(
        f"{server['url']}/v1/{what}", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {server['token']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or b"{}").get("error") or e.reason
        except ValueError:
            why = e.reason
        raise Unreachable(f"the memory server refused the request ({e.code}: {why})") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unreachable(f"the memory server at {server['url']} couldn't be reached "
                          f"({type(e).__name__}: {getattr(e, 'reason', e)})") from e


# -- turns this machine holds until the server can be reached --------------------------------

BATCH = 200


class Outbox:
    """Turns, and ends of sessions, that this machine holds until it reaches the mind's server
    (docs/primitives/MIND.md: a mind is behind only while a machine holds turns it couldn't add).

    Kept in a file of one JSON line each, so they outlive the process and a restart. Adding is
    an append under a short lock, so it never waits on the network; one sender at a time, in any
    process, sends them in order and drops each once the server has it. A turn sent twice is
    added once, since the server keys turns.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _flock(self, suffix: str, *, wait: bool = True):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path.with_name(self.path.name + suffix), "a")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            fh.close()
            return None
        return fh

    def add(self, item: dict) -> None:
        lock = self._flock(".lock")
        try:
            # What was said is private, so only the owner of this machine's account can read it.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(json.dumps(item) + "\n")
        finally:
            lock.close()

    def pending(self) -> list[dict]:
        try:
            return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        except FileNotFoundError:
            return []

    def waiting(self) -> int:
        """How many turns are waiting."""
        return sum(len(i.get("turns") or []) for i in self.pending())

    def send(self, server: dict, *, timeout: float = 20.0) -> str | None:
        """Send what is held, in order, until the server has it all or refuses. Returns why it
        stopped short, or None. Another sender already at it counts as sending."""
        sender = self._flock(".send", wait=False)
        if sender is None:
            return None
        try:
            lock = self._flock(".lock")
            try:
                items = self.pending()
            finally:
                lock.close()
            done, why = 0, None
            while done < len(items):
                item = items[done]
                try:
                    if item.get("end"):
                        call(server, "end", {"session": item["session"], "place": item.get("place")}, timeout=timeout)
                        n = 1
                    else:
                        # Consecutive turns of one session go together.
                        n, turns = 0, []
                        while (done + n < len(items) and not items[done + n].get("end")
                               and items[done + n]["session"] == item["session"] and len(turns) < BATCH):
                            turns += items[done + n]["turns"]
                            n += 1
                        call(server, "turns", {"session": item["session"], "place": item.get("place"), "turns": turns},
                             timeout=timeout)
                except Unreachable as e:
                    why = str(e)
                    break
                done += n
            if done:
                # Only appends happen meanwhile, so what was sent is still the file's first lines.
                lock = self._flock(".lock")
                try:
                    rest = self.path.read_text().splitlines(keepends=True)[done:]
                    tmp = self.path.with_name(self.path.name + ".tmp")
                    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                        fh.write("".join(rest))
                    tmp.replace(self.path)
                finally:
                    lock.close()
            return why
        finally:
            sender.close()


def behind_notice(waiting: int, why: str | None) -> str:
    if not waiting:
        return ""
    return (f"[MEMORY IS BEHIND: {waiting} {'turn' if waiting == 1 else 'turns'} from this machine haven't reached "
            f"the memory server yet{f' ({why})' if why else ''}. They are kept here and added as soon as it can be "
            "reached; until then, recall doesn't know what was said in them.]")
