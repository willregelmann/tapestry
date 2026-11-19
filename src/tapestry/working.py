"""Working memory: what the agent is keeping in view for now (docs/primitives/WORKING_MEMORY.md).

Rules this module enforces:
- One is shown when its topic is searched, or a topic above it, and nowhere else.
- It is written, rewritten and removed only when asked. Nothing here is called from the
  background, and nothing here touches a memory, its evidence or an association.
- Rewriting replaces what it says and keeps nothing of what it said.
- Once its end has passed it is never shown, and it is deleted the next time anything is written.
- Rewriting leaves its end where it was unless a new one is given.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from typing import TYPE_CHECKING, Iterable

from tapestry.mind import GENERAL, within

if TYPE_CHECKING:
    from tapestry.mind import Mind

# Kept out of the mind's own schema, so a mind that has never held working memory is still one
# the viewer can read without changing it.
_TABLE = """
CREATE TABLE IF NOT EXISTS working_memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- never reused: a removed one's number can't come to mean another
    topic_id   INTEGER REFERENCES topics(id),       -- NULL: the general topic
    content    TEXT NOT NULL,
    written_at REAL NOT NULL,
    until      REAL
)"""
KEEP = object()   # rewrite: leave the end where it was


def end_of(until: str | float | None) -> float | None:
    """When something given as an end falls: a time as it is, a day as the end of that day
    here, where the user is. ValueError for anything else."""
    if until is None or isinstance(until, (int, float)):
        return until
    text = until.strip()
    try:
        if len(text) == 10:
            day = dt.date.fromisoformat(text)
            return dt.datetime.combine(day, dt.time(23, 59, 59)).astimezone().timestamp()
        return dt.datetime.fromisoformat(text).astimezone().timestamp()
    except ValueError:
        raise ValueError(f"an end is a day (2026-11-20) or a time (2026-11-20T17:00), not {until!r}") from None


def _exists(mind: "Mind") -> bool:
    return mind.db.execute("SELECT 1 FROM sqlite_master WHERE name='working_memories'").fetchone() is not None


def _row(r) -> dict:
    return {"id": r[0], "topic": r[1] or GENERAL, "content": r[2], "written_at": r[3], "until": r[4]}


def _sweep(mind: "Mind", now: float) -> None:
    mind.db.execute(_TABLE)
    mind.db.execute("DELETE FROM working_memories WHERE until IS NOT NULL AND until <= ?", (now,))


def write(mind: "Mind", content: str, *, topic: str | None = None, until: str | float | None = None,
          now: float | None = None) -> dict:
    """Keep something in view in `topic`, or in the general topic, until `until` if given."""
    now = time.time() if now is None else now
    content = (content or "").strip()
    if not content:
        raise ValueError("a working memory says something")
    end = end_of(until)
    if end is not None and end <= now:
        raise ValueError("that end has already passed")
    tid = None
    if topic and topic != GENERAL:
        row = mind.db.execute("SELECT id FROM topics WHERE name=?", (topic,)).fetchone()
        if row is None:
            raise ValueError(f"no topic {topic!r}; describe it first to create it")
        tid = row[0]
    _sweep(mind, now)
    wid = mind.db.execute("INSERT INTO working_memories(topic_id, content, written_at, until) VALUES (?,?,?,?)",
                          (tid, content, now, end)).lastrowid
    return get(mind, wid, now=now)


def rewrite(mind: "Mind", wid: int, content: str, *, until=KEEP, now: float | None = None) -> dict:
    """Replace what one says. Its end stays where it was unless `until` is given; None takes the end away."""
    now = time.time() if now is None else now
    content = (content or "").strip()
    if not content:
        raise ValueError("a working memory says something; remove it instead")
    get(mind, wid, now=now)
    if until is KEEP:
        mind.db.execute("UPDATE working_memories SET content=?, written_at=? WHERE id=?", (content, now, wid))
    else:
        end = end_of(until)
        if end is not None and end <= now:
            raise ValueError("that end has already passed")
        mind.db.execute("UPDATE working_memories SET content=?, written_at=?, until=? WHERE id=?",
                        (content, now, end, wid))
    _sweep(mind, now)
    return get(mind, wid, now=now)


def remove(mind: "Mind", wid: int, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    get(mind, wid, now=now)
    mind.db.execute("DELETE FROM working_memories WHERE id=?", (wid,))
    _sweep(mind, now)


def get(mind: "Mind", wid: int, *, now: float | None = None) -> dict:
    """One working memory that hasn't ended. KeyError if there is none."""
    now = time.time() if now is None else now
    row = _exists(mind) and mind.db.execute(
        "SELECT w.id, t.name, w.content, w.written_at, w.until FROM working_memories w"
        " LEFT JOIN topics t ON t.id = w.topic_id WHERE w.id=? AND (w.until IS NULL OR w.until > ?)",
        (wid, now)).fetchone()
    if not row:
        raise KeyError(f"no working memory w{wid}")
    return _row(row)


def in_view(mind: "Mind", topics: Iterable[str] = (), *, now: float | None = None) -> list[dict]:
    """The working memory shown to a lookup over the general topic and `topics`: theirs, and that
    of every topic under them, the general topic's first and the oldest first within each."""
    now = time.time() if now is None else now
    searched = list(dict.fromkeys(topics))
    if not _exists(mind):
        return []
    rows = [_row(r) for r in mind.db.execute(
        "SELECT w.id, t.name, w.content, w.written_at, w.until FROM working_memories w"
        " LEFT JOIN topics t ON t.id = w.topic_id WHERE w.until IS NULL OR w.until > ?"
        " ORDER BY t.name IS NOT NULL, t.name, w.id", (now,))]
    return [r for r in rows if r["topic"] == GENERAL or any(within(r["topic"], t) for t in searched)]


def held(mind: "Mind", topics: Iterable[str], *, now: float | None = None) -> list[dict]:
    """The working memory of exactly these topics and what is under them, where GENERAL names the
    general topic itself: what an owner looking at those topics is shown."""
    names = list(dict.fromkeys(topics))
    every = in_view(mind, [n for n in names if n != GENERAL], now=now)
    return [r for r in every if r["topic"] != GENERAL or GENERAL in names]
