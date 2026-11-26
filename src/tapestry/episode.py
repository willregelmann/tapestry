"""An episode: something a mind met, as it happened, kept for a while (docs/primitives/EPISODE.md).

A conversation is one session's turns, word for word. A reading is a source the agent chose,
as it read when it was read: its passages are its turns, said by the source.

Rules this module enforces:
- An episode is never a memory. Its turns live apart from memories, and recall never reads them.
- A reading's words are the source's, never the user's or the agent's, and it is asked for in a
  session; it waits to be drawn until that session has ended.
- A turn is added once and never edited. A turn that has gone, expired or forgotten, is
  never added again.
- An episode goes only after it has ended, every turn has had its facts drawn, and its
  time is up.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from tapestry.mind import Mind

KEEP = 30 * 86400          # how long an episode is kept once it ends: a tuning decision
IDLE = 30 * 60             # an episode untouched this long has ended, said so or not
SPEAKERS = ("user", "agent")
SOURCE = "source"          # who says a reading's passages
_COLUMNS = "id, place, kind, address, title, topic, session"


class Episode:
    def __init__(self, mind: "Mind", id: str, place: str | None, kind: str = "conversation",
                 address: str | None = None, title: str | None = None, topic: str | None = None,
                 session: str | None = None) -> None:
        self.mind, self.id, self.place, self.kind = mind, id, place, kind
        self.address, self.title, self.topic, self.session = address, title, topic, session

    @classmethod
    def _load(cls, mind: "Mind", episode_id: str) -> "Episode":
        row = mind.db.execute(f"SELECT {_COLUMNS} FROM episodes WHERE id=?", (episode_id,)).fetchone()
        return cls(mind, *row)

    @property
    def reading(self) -> bool:
        return self.kind == "reading"

    @classmethod
    def read(cls, mind: "Mind", session_id: str, *, address: str, title: str, topic: str | None,
             passages: list[tuple[str, str]], place: str | None = None, at: float | None = None) -> "Episode":
        """A source the agent chose, read now: its passages, said by the source, and closed at
        once. Asking for the same source twice in one session gives the one reading."""
        if not session_id:
            raise ValueError("a reading is asked for in a session, and this one has no id")
        if not passages:
            raise ValueError(f"nothing was read from {address}")
        at = time.time() if at is None else at
        rid = "reading:" + hashlib.sha256(f"{session_id}\0{address}".encode()).hexdigest()[:24]
        db = mind.db
        with db:
            db.execute("BEGIN IMMEDIATE")
            fresh = db.execute(
                "INSERT OR IGNORE INTO episodes(id, place, started_at, ended_at, kind, address, title, topic, session)"
                " VALUES (?,?,?,?,'reading',?,?,?,?)", (rid, place, at, at, address, title, topic, session_id)).rowcount
            if fresh:
                db.executemany("INSERT OR IGNORE INTO turns(episode, key, source, text, ts, via) VALUES (?,?,?,?,?,?)",
                               [(rid, f"{rid}:{n}", SOURCE, f"{heading}\n{text}" if heading else text, at, mind.via)
                                for n, (heading, text) in enumerate(passages, 1)])
        return cls._load(mind, rid)

    @classmethod
    def begin(cls, mind: "Mind", session_id: str, place: str | None = None) -> "Episode":
        """The episode of this session, begun now if it's new. A place given later fills one
        that wasn't known, and never moves an episode from the place it happened in."""
        if not session_id:
            raise ValueError("an episode belongs to a session, and this one has no id")
        mind.db.execute("INSERT OR IGNORE INTO episodes(id, place, started_at) VALUES (?,?,?)",
                        (session_id, place, time.time()))
        if place:
            mind.db.execute("UPDATE episodes SET place=? WHERE id=? AND place IS NULL", (place, session_id))
        return cls._load(mind, session_id)

    def add_turn(self, key: str, source: str, text: str, ts: float | None = None) -> bool:
        """Add what one speaker said. Returns False when this turn was already added, or has gone."""
        if self.reading:
            raise ValueError("a reading is read once and never added to")
        if source not in SPEAKERS:
            raise ValueError(f"a turn is said by {SPEAKERS}, not {source!r}")
        if not text or not text.strip():
            return False
        db = self.mind.db
        if db.execute("SELECT 1 FROM spent_turns WHERE key=?", (key,)).fetchone():
            return False
        return db.execute("INSERT OR IGNORE INTO turns(episode, key, source, text, ts, via) VALUES (?,?,?,?,?,?)",
                          (self.id, key, source, text, time.time() if ts is None else ts,
                           self.mind.via)).rowcount == 1

    def add_recalled(self, memory_ids: Iterable[int], ts: float | None = None) -> None:
        """Record which memories were in mind at this moment."""
        ts = time.time() if ts is None else ts
        self.mind.db.executemany("INSERT OR IGNORE INTO recalled VALUES (?,?,?)",
                                 [(self.id, m, ts) for m in dict.fromkeys(memory_ids)])

    def end(self, at: float | None = None) -> None:
        self.mind.db.execute("UPDATE episodes SET ended_at=? WHERE id=?",
                             (time.time() if at is None else at, self.id))

    def turns(self, after: int = 0) -> list[dict]:
        cur = self.mind.db.execute("SELECT seq, key, source, text, ts FROM turns WHERE episode=? AND seq>?"
                                   " ORDER BY seq", (self.id, after))
        return [dict(zip(("seq", "key", "source", "text", "ts"), r)) for r in cur]

    @property
    def drawn_through(self) -> int:
        return self.mind.db.execute("SELECT drawn_through FROM episodes WHERE id=?", (self.id,)).fetchone()[0]

    def mark_drawn(self, through: int) -> None:
        """Facts have been drawn from every turn up to `through`."""
        self.mind.db.execute("UPDATE episodes SET drawn_through=max(drawn_through, ?) WHERE id=?",
                             (through, self.id))

    def recalled_between(self, start: float, end: float) -> list[int]:
        """The current memories that were in mind from `start` to `end`."""
        return [r[0] for r in self.mind.db.execute(
            "SELECT DISTINCT r.memory_id FROM recalled r JOIN memories m ON m.id = r.memory_id"
            " WHERE r.episode=? AND r.ts BETWEEN ? AND ? AND m.superseded_by IS NULL",
            (self.id, start, end))]

    def noted_this_moment(self) -> list[int]:
        """Memories noted in this session since its last recorded turn: the same moment."""
        last = self.mind.db.execute("SELECT max(ts) FROM turns WHERE episode=?", (self.id,)).fetchone()[0]
        return [r[0] for r in self.mind.db.execute(
            "SELECT memory_id FROM evidence WHERE kind='observe' AND episode=? AND ts>?",
            (self.id, last if last is not None else float("-inf")))]


def words_of(mind: "Mind", memory_id: int) -> list[dict]:
    """The words a memory was drawn from, in the episodes that still keep them: where it came
    from, never something remembered (docs/primitives/EPISODE.md)."""
    cur = mind.db.execute("SELECT t.episode, t.source, t.text, t.ts FROM drawn_from d JOIN turns t ON t.key = d.turn_key"
                          " WHERE d.memory_id=? ORDER BY t.seq", (memory_id,))
    return [dict(zip(("episode", "source", "text", "ts"), r)) for r in cur]


def waiting(mind: "Mind", *, now: float | None = None, idle: float = IDLE) -> list[Episode]:
    """Episodes that have ended, said so or not, with turns no facts have been drawn from. A
    reading waits until the session that asked for it has ended too."""
    now = time.time() if now is None else now
    ended = ("(e.ended_at IS NOT NULL OR (SELECT max(ts) FROM turns t WHERE t.episode = e.id) < :idle)")
    rows = mind.db.execute(
        f"SELECT {', '.join('e.' + c.strip() for c in _COLUMNS.split(','))} FROM episodes e"
        " WHERE EXISTS (SELECT 1 FROM turns t WHERE t.episode = e.id AND t.seq > e.drawn_through)"
        f" AND {ended} AND (e.kind != 'reading' OR NOT EXISTS (SELECT 1 FROM episodes s WHERE s.id = e.session"
        "   AND s.ended_at IS NULL AND (SELECT max(ts) FROM turns t WHERE t.episode = s.id) >= :idle))"
        " ORDER BY e.started_at", {"idle": now - idle}).fetchall()
    return [Episode(mind, *r) for r in rows]


def expire(mind: "Mind", *, now: float | None = None, keep: float = KEEP, idle: float = IDLE) -> int:
    """Let episodes whose time is up go entirely. Never one with turns still to be drawn.
    Returns how many went."""
    now = time.time() if now is None else now
    gone = [r[0] for r in mind.db.execute(
        "SELECT e.id FROM episodes e WHERE"
        " NOT EXISTS (SELECT 1 FROM turns t WHERE t.episode = e.id AND t.seq > e.drawn_through)"
        " AND coalesce(e.ended_at, (SELECT max(ts) + ? FROM turns t WHERE t.episode = e.id), e.started_at + ?)"
        " < ?", (idle, idle, now - keep))]
    if not gone:
        return 0
    with mind.db:
        mind.db.execute("BEGIN IMMEDIATE")
        for eid in gone:
            mind.db.execute("INSERT OR IGNORE INTO spent_turns SELECT key FROM turns WHERE episode=?", (eid,))
            mind.db.execute("DELETE FROM drawn_from WHERE turn_key IN (SELECT key FROM turns WHERE episode=?)",
                            (eid,))
            mind.db.execute("DELETE FROM turns WHERE episode=?", (eid,))
            mind.db.execute("DELETE FROM recalled WHERE episode=?", (eid,))
            mind.db.execute("DELETE FROM episodes WHERE id=?", (eid,))
    mind.scrub()
    return len(gone)
