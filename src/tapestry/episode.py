"""An episode: one session's conversation, word for word, kept for a while (docs/primitives/EPISODE.md).

Rules this module enforces:
- An episode is never a memory. Its turns live apart from memories, and recall never reads them.
- A turn is added once and never edited. A turn that has gone, expired or forgotten, is
  never added again.
- An episode goes only after it has ended, every turn has had its facts drawn, and its
  time is up.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from tapestry.mind import Mind

KEEP = 30 * 86400          # how long an episode is kept once it ends: a tuning decision
IDLE = 30 * 60             # an episode untouched this long has ended, said so or not
SPEAKERS = ("user", "agent")


class Episode:
    def __init__(self, mind: "Mind", id: str, place: str | None) -> None:
        self.mind, self.id, self.place = mind, id, place

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
        row = mind.db.execute("SELECT place FROM episodes WHERE id=?", (session_id,)).fetchone()
        return cls(mind, session_id, row[0])

    def add_turn(self, key: str, source: str, text: str, ts: float | None = None) -> bool:
        """Add what one speaker said. Returns False when this turn was already added, or has gone."""
        if source not in SPEAKERS:
            raise ValueError(f"a turn is said by {SPEAKERS}, not {source!r}")
        if not text or not text.strip():
            return False
        db = self.mind.db
        if db.execute("SELECT 1 FROM spent_turns WHERE key=?", (key,)).fetchone():
            return False
        return db.execute("INSERT OR IGNORE INTO turns(episode, key, source, text, ts) VALUES (?,?,?,?,?)",
                          (self.id, key, source, text, time.time() if ts is None else ts)).rowcount == 1

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


def waiting(mind: "Mind", *, now: float | None = None, idle: float = IDLE) -> list[Episode]:
    """Episodes that have ended, said so or not, with turns no facts have been drawn from."""
    now = time.time() if now is None else now
    rows = mind.db.execute(
        "SELECT e.id, e.place FROM episodes e WHERE EXISTS (SELECT 1 FROM turns t WHERE t.episode = e.id"
        " AND t.seq > e.drawn_through) AND (e.ended_at IS NOT NULL OR"
        " (SELECT max(ts) FROM turns t WHERE t.episode = e.id) < ?) ORDER BY e.started_at",
        (now - idle,)).fetchall()
    return [Episode(mind, i, p) for i, p in rows]


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
        mind.db.execute("BEGIN")
        for eid in gone:
            mind.db.execute("INSERT OR IGNORE INTO spent_turns SELECT key FROM turns WHERE episode=?", (eid,))
            mind.db.execute("DELETE FROM drawn_from WHERE turn_key IN (SELECT key FROM turns WHERE episode=?)",
                            (eid,))
            mind.db.execute("DELETE FROM turns WHERE episode=?", (eid,))
            mind.db.execute("DELETE FROM recalled WHERE episode=?", (eid,))
            mind.db.execute("DELETE FROM episodes WHERE id=?", (eid,))
    mind.scrub()
    return len(gone)
