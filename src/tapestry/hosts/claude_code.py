"""Claude Code (and Agent SDK) adapter: hook handlers over one mind per user.

Claude Code has no agent identity, so the mind belongs to the user
(invariants/interfaces/HOST.md). A session searches the default namespace
plus the namespace of the project it runs in, named after the repository's
root folder. Hooks run as short-lived processes, so this module holds no state
between calls. The one piece of session state, extra namespaces the agent
opened, is kept per project in a small JSON file.

Moments:
    SessionStart      standing guidance, and a loud marker if memory is broken
    UserPromptSubmit  recall for the prompt, as additional context
    Stop / PreCompact / SessionEnd
                      remember the transcript's new user and assistant text,
                      in a detached process so the session never waits
    PostToolUse       a write to Claude Code's own memory files is recorded
                      as host_memory evidence
"""

from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

from tapestry import render
from tapestry.mind import Mind, RecallFailed

TRIVIAL = re.compile(
    r"^(yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|yeah|nah|hi|hey|hello|"
    r"continue|go ahead|do it|proceed|got it|cool|nice|great|done|next|lgtm|k|agreed)"
    r"[\s!?.,:;'\"~)(*]*$", re.IGNORECASE)
# Text Claude Code puts in the user role that the user didn't type.
_HARNESS_TEXT = re.compile(r"^\s*(<(system-reminder|command-name|command-message|command-args|"
                           r"local-command-stdout|local-command-stderr|task-notification|"
                           r"user-memory-input)\b|\[Request interrupted)")


def home() -> Path:
    return Path(os.environ.get("TAPESTRY_HOME") or Path.home() / ".tapestry")


def owner() -> str:
    return os.environ.get("TAPESTRY_OWNER") or getpass.getuser()


def mind_path() -> Path:
    return home() / "minds" / f"{owner()}.db"


def project_namespace(cwd: str | None) -> str | None:
    """The namespace for the project containing `cwd`: its git root's folder name,
    lowercased to a valid namespace name."""
    if not cwd:
        return None
    path = Path(cwd).resolve()
    if path == Path.home().resolve():
        return None
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return _ns_name(p.name)
        if p == Path.home():
            break
    return _ns_name(path.name)


def _ns_name(folder: str) -> str | None:
    name = re.sub(r"[^a-z0-9._-]+", "-", folder.lower()).strip("-._")
    return name or None


def _project_root(cwd: str) -> Path:
    path = Path(cwd).resolve()
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return path


def _state_file(cwd: str | None) -> Path:
    key = hashlib.sha256(str(Path(cwd or ".").resolve()).encode()).hexdigest()[:16]
    return home() / "state" / f"namespaces-{key}.json"


def open_namespaces(cwd: str | None) -> list[str]:
    """The project's namespace, plus any the agent opened in this project."""
    proj = project_namespace(cwd)
    names = [proj] if proj else []
    try:
        extra = json.loads(_state_file(cwd).read_text())
    except (OSError, ValueError):
        extra = []
    return names + [n for n in extra if n not in names]


def save_open_namespaces(cwd: str | None, names: list[str]) -> None:
    proj = project_namespace(cwd)
    f = _state_file(cwd)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([n for n in names if n != proj]))


def open_mind(cwd: str | None = None) -> Mind:
    from tapestry import embed
    path = mind_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    mind = Mind(path, encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
    proj = project_namespace(cwd)
    if proj and not any(n["name"] == proj for n in mind.namespaces()):
        mind.namespace(proj, f"The {proj} project ({_project_root(cwd)})")
    return mind


def unavailable(why: str) -> str:
    return (f"[MEMORY UNAVAILABLE: {why}. No memories were searched. Don't treat this as "
            "evidence that memory holds nothing relevant; it wasn't consulted.]")


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


# -- hook handlers -------------------------------------------------------------

def on_session_start(inp: dict) -> dict:
    try:
        mind = open_mind(inp.get("cwd"))
        try:
            listing = mind.namespaces()
        finally:
            mind.close()
    except Exception as e:
        return _context("SessionStart", unavailable(f"tapestry couldn't open ({type(e).__name__}: {e})"))
    opened = open_namespaces(inp.get("cwd"))
    return _context("SessionStart", "Long-term memory (tapestry) is on. Searching the default "
                                    f"namespace{' plus ' + ', '.join(opened) if opened else ''}. "
                                    + render.GUIDE + render.directory(listing, opened))


def on_prompt(inp: dict) -> dict | None:
    prompt = (inp.get("prompt") or "").strip()
    if not prompt or prompt.startswith("/") or TRIVIAL.match(prompt):
        return None
    try:
        mind = open_mind(inp.get("cwd"))
    except Exception as e:
        return _context("UserPromptSubmit", unavailable(f"tapestry couldn't open ({type(e).__name__}: {e})"))
    try:
        hits = mind.recall(prompt, namespaces=open_namespaces(inp.get("cwd")), k=5)
    except RecallFailed as e:
        return _context("UserPromptSubmit", unavailable(f"recall failed ({e})"))
    finally:
        mind.close()
    if not hits:
        return None
    return _context("UserPromptSubmit", "Recalled from long-term memory:\n" + render.block(hits))


def on_transcript(inp: dict) -> None:
    """Stop, PreCompact, SessionEnd: remember in the background; never block the session."""
    path = inp.get("transcript_path")
    if not path:
        return None
    log = home() / "logs" / "ingest.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as fh:
        subprocess.Popen([sys.executable, "-m", "tapestry.cli", "ingest", path,
                          "--cwd", inp.get("cwd") or "", "--session", inp.get("session_id") or ""],
                         stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True)
    return None


def _is_host_memory(file_path: str) -> bool:
    p = Path(file_path)
    projects = Path.home() / ".claude" / "projects"
    return (p.suffix == ".md" and p.name != "MEMORY.md" and p.parent.name == "memory"
            and projects in p.parents)


def on_tool_use(inp: dict) -> None:
    """A write to Claude Code's own memory files: the agent's notebook, as evidence."""
    fp = (inp.get("tool_input") or {}).get("file_path") or ""
    if not fp or not _is_host_memory(fp):
        return None
    try:
        content = Path(fp).read_text().strip()
    except OSError:
        return None
    if not content:
        return None
    mind = open_mind(inp.get("cwd"))
    try:
        mind.remember(content, source="host_memory",
                      key="cc-memory:" + hashlib.sha256(f"{fp}\0{content}".encode()).hexdigest(),
                      episode=inp.get("session_id"))
    finally:
        mind.close()
    return None


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_prompt,
    "Stop": on_transcript,
    "PreCompact": on_transcript,
    "SessionEnd": on_transcript,
    "PostToolUse": on_tool_use,
}


def run_hook(stdin: str) -> str:
    """Handle one hook call. Returns what to print. Never raises into Claude Code."""
    try:
        inp = json.loads(stdin or "{}")
        out = HANDLERS.get(inp.get("hook_event_name", ""), lambda _: None)(inp)
        return json.dumps(out) if out else ""
    except Exception as e:
        event = "UserPromptSubmit"
        try:
            event = json.loads(stdin).get("hook_event_name") or event
        except Exception:
            pass
        if event in ("SessionStart", "UserPromptSubmit"):
            return json.dumps(_context(event, unavailable(f"{type(e).__name__}: {e}")))
        print(f"tapestry {event} hook failed: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


# -- transcript ingestion -------------------------------------------------------

def _texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            out.append(block["text"])
    return out


def transcript_entries(path: str) -> Iterator[tuple[str, str, str, float]]:
    """(uuid, source, text, timestamp) for what the user typed and the assistant wrote.

    Subagent (sidechain) entries, tool traffic, meta entries and text the harness
    injected into the user role are skipped: they aren't observations of the
    conversation between the user and the agent.
    """
    with open(path) as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            if kind not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
                continue
            text = "\n".join(t for t in _texts((e.get("message") or {}).get("content"))
                             if not _HARNESS_TEXT.match(t)).strip()
            if not text:
                continue
            try:
                ts = dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                ts = None
            yield e.get("uuid") or hashlib.sha256(line.encode()).hexdigest(), \
                ("user" if kind == "user" else "agent"), text, ts


def ingest(path: str, cwd: str | None, session: str | None) -> dict:
    names = open_namespaces(cwd)
    mind = open_mind(cwd)
    stats = {"remembered": 0, "already": 0}
    try:
        for uuid, source, text, ts in transcript_entries(path):
            key = f"cc:{uuid}"
            if mind.db.execute("SELECT 1 FROM memories WHERE key=?", (key,)).fetchone():
                stats["already"] += 1
                continue
            try:
                mind.remember(text, source=source, namespaces=names, at=ts, key=key,
                              episode=session or None)
                stats["remembered"] += 1
            except sqlite3.IntegrityError:  # a concurrent ingest got there first
                stats["already"] += 1
    finally:
        mind.close()
    return stats
