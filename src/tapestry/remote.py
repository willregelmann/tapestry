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
