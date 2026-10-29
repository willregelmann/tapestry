"""A mind: one owner's memories, episodes, associations and evidence, in one SQLite file.

Memory and Recall (docs/primitives/MEMORY.md, docs/capabilities/RECALL.md).
Belief is read from a memory's evidence (tapestry.belief) and never stored.
Episodes live in tapestry.episode and are never read by recall.

Rules this module enforces:
- Memory content is never edited. A newer memory supersedes an older one, and
  the older stays in history, never served as current.
- Every memory is born with evidence naming its source.
- Remembering the same thing twice is idempotent.
- Every association is born with a fact (docs/primitives/ASSOCIATION.md): the only
  way one is made is in remember(), and use only changes how strong it is.
- Recall sees the general topic plus the topics it's given, and a topic it isn't
  given affects nothing, associations into it included.
- Forgetting is complete: the memory, its evidence, its associations, its place in
  the full-text index and the words it came from in every episode go, and nothing
  left behind says what it held. The mind remembers only that something was forgotten.
- A recall that can't search says so. It raises; it never returns an empty
  list that looks like "nothing relevant".
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from tapestry import belief

Encode = Callable[[list[str]], np.ndarray]

SCHEMA_VERSION = 3

# Match bands, carried over from mnemonic's measured corpus (BANDS.md) until
# Stage 0's harness says otherwise. They describe topical match, never truth.
MATCH_WITHHOLD = 0.15
MATCH_OK = 0.45
RRF_K = 60
# Weight of the keyword ranking in fusion (semantic is 1.0). Measured on the
# Stage 0 harness: on short facts keyword ranking only hurts (basic MRR .93 at
# 0, .75 at 1), while on long LongMemEval sessions it helps (multi-session
# hit@1 .70 at 0, .90 at 0.5 and 1; temporal .30 vs .60). 0.5 keeps the long-
# session gains and loses less on facts. Small samples: revisit with more data.
KEYWORD_WEIGHT = 0.5

# Associations. A new one starts halfway; use moves it toward 1 and disuse halves it
# over ASSOCIATION_HALF_LIFE. Recall follows one only above FOLLOW, and the strength
# flowing into any one memory is scaled down past INFLOW_CAP, so popularity alone
# can't pull a memory into every recall. Untuned.
BORN_STRENGTH = 0.5
STRENGTHEN = 0.2
ASSOCIATION_HALF_LIFE = 180 * belief.DAY
FOLLOW = 0.25
INFLOW_CAP = 2.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- A memory in no topic lives in the general topic, which is always searched.
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY,
    content         TEXT NOT NULL,
    key             TEXT NOT NULL UNIQUE,      -- idempotency key
    created_at      REAL NOT NULL,             -- when it was learned
    superseded_by   INTEGER REFERENCES memories(id),
    embedding       BLOB,
    embedding_model TEXT,
    volatility      TEXT
);
CREATE TABLE IF NOT EXISTS memory_topics (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    topic_id  INTEGER NOT NULL REFERENCES topics(id),
    PRIMARY KEY (memory_id, topic_id)
);
CREATE INDEX IF NOT EXISTS memory_topics_topic ON memory_topics(topic_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(content, content=memories, content_rowid=id);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TABLE IF NOT EXISTS associations (
    src          INTEGER NOT NULL REFERENCES memories(id),
    dst          INTEGER NOT NULL REFERENCES memories(id),
    strength     REAL NOT NULL CHECK (strength BETWEEN 0 AND 1),
    last_used_at REAL NOT NULL,
    PRIMARY KEY (src, dst)
);
-- What a forgotten memory leaves: that it existed, never what it held. The key stops
-- the same fact being recorded again.
CREATE TABLE IF NOT EXISTS forgotten (
    id  INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    at  REAL NOT NULL
);
-- What a model was asked about a pair of memories, who answered, and what it said, in coarse
-- categories (docs/primitives/EVIDENCE.md). `judged` marks a memory whose pairs are all done.
CREATE TABLE IF NOT EXISTS judgments (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    other_id  INTEGER NOT NULL REFERENCES memories(id),
    prompt    TEXT NOT NULL,
    model     TEXT NOT NULL,
    category  TEXT NOT NULL CHECK (category IN ('conflict','maybe','no conflict')),
    at        REAL NOT NULL,
    PRIMARY KEY (memory_id, other_id, prompt)
);
CREATE TABLE IF NOT EXISTS judged (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id),
    prompt    TEXT NOT NULL,
    at        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY,
    memory_id   INTEGER NOT NULL REFERENCES memories(id),
    kind        TEXT NOT NULL CHECK (kind IN
                  ('observe','confirm','contradict','supersede','file','unfile')),
    source      TEXT NOT NULL,
    reliability REAL,
    strength    REAL NOT NULL DEFAULT 1.0,
    episode     TEXT,
    trigger     TEXT,
    reasoner    TEXT,
    note        TEXT,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_memory ON evidence(memory_id);
-- Episodes (docs/primitives/EPISODE.md): a session's conversation, word for word, kept for a
-- while. `drawn_through` is the last turn facts have been drawn from.
CREATE TABLE IF NOT EXISTS episodes (
    id            TEXT PRIMARY KEY,
    place         TEXT,
    started_at    REAL NOT NULL,
    ended_at      REAL,
    drawn_through INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turns (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    episode TEXT NOT NULL REFERENCES episodes(id),
    key     TEXT NOT NULL UNIQUE,
    source  TEXT NOT NULL,
    text    TEXT NOT NULL,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_episode ON turns(episode, seq);
-- Which memories were recalled into an episode, and when.
CREATE TABLE IF NOT EXISTS recalled (
    episode   TEXT NOT NULL REFERENCES episodes(id),
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    ts        REAL NOT NULL,
    PRIMARY KEY (episode, memory_id, ts)
);
-- The turns a memory was drawn from, so forgetting it can take their words.
CREATE TABLE IF NOT EXISTS drawn_from (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    turn_key  TEXT NOT NULL,
    PRIMARY KEY (memory_id, turn_key)
);
-- Turns that were added once and have gone, expired or forgotten: only their keys, so
-- a transcript read again doesn't bring them back.
CREATE TABLE IF NOT EXISTS spent_turns (key TEXT PRIMARY KEY);
-- Content is never edited and evidence is never rewritten.
CREATE TRIGGER IF NOT EXISTS memories_no_edit BEFORE UPDATE OF content ON memories
BEGIN SELECT RAISE(ABORT, 'memory content is never edited; supersede it'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_edit BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is never edited'); END;
CREATE TRIGGER IF NOT EXISTS turns_no_edit BEFORE UPDATE ON turns
BEGIN SELECT RAISE(ABORT, 'an episode is never edited'); END;
-- Runtimes from before topics and episodes refuse any file with this name in it. Without
-- it they would quietly recreate their old tables here and file turns as memories again.
CREATE VIEW IF NOT EXISTS scopes AS SELECT 1 WHERE 0;
"""

SOURCES = ("user", "user_confirmed", "tool", "web", "agent", "inference", "host_memory")
GENERAL = "general"

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be been being by can did do does for from had has have "
    "how i if in is it its me my no not of on or our out so than that the their "
    "them then there these they this those to too was we were what when where "
    "which while who why will with would you your about please".split())


class RecallFailed(RuntimeError):
    """Recall couldn't search. Never to be reported as 'nothing found'."""


@dataclass(frozen=True)
class Forgotten:
    ids: list[int]        # the memory forgotten
    restored: list[int] = field(default_factory=list)  # superseded by a forgotten memory, current again


@dataclass(frozen=True)
class Recalled:
    id: int
    content: str
    topics: tuple[str, ...]   # empty: the general topic
    score: float           # fused rank score; only its order means anything
    cosine: float          # topical similarity to the query
    via: str               # "semantic", "keyword", "both", "conflict" or "association"
    created_at: float
    belief: belief.Belief | None = None   # how much it is believed; a different question from match
    conflicts: tuple[int, ...] = ()       # current memories a model found in conflict with it
    brought_by: int | None = None         # the memory whose association brought it to mind

    @property
    def match(self) -> str:
        if self.via == "keyword" and self.cosine < MATCH_WITHHOLD:
            return "keyword only"
        if self.cosine >= MATCH_OK:
            return "ok"
        return "LOW"


def _pack(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype="<f4").tobytes()


def topic_name(name: str) -> str:
    """A valid topic name, or ValueError."""
    name = name.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
        raise ValueError(f"topic names are lowercase letters, digits, '.', '_' or '-': {name!r}")
    if name == GENERAL:
        raise ValueError(f"{GENERAL!r} is the general topic, which every lookup searches; "
                         "file nothing under it to put a memory there")
    return name


class Mind:
    """One owner's mind. Usable from any thread, but not concurrently: callers
    that share one serialize access (SQLite WAL lets separate Minds on the same
    file read while another writes)."""

    def __init__(self, path: Path | str, *, encode: Encode, model_tag: str) -> None:
        self.path = Path(path)
        self._encode = encode
        self._model_tag = model_tag
        self.db = sqlite3.connect(str(self.path), isolation_level=None,
                                  check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        # Deleted pages are zeroed, so a forgotten memory isn't left readable in the file.
        self.db.execute("PRAGMA secure_delete=ON")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='scopes' AND type='table'").fetchone():
            raise RuntimeError(f"{self.path} uses the pre-namespace development schema (v1); "
                               "it can't be opened by this version")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='namespaces' AND type='table'").fetchone():
            from tapestry import migrate
            migrate.to_topics_and_episodes(self)
        self.db.executescript(_SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))

    def episode(self, session_id: str, place: str | None = None):
        """The episode of this session, begun if it's new (docs/primitives/EPISODE.md)."""
        from tapestry.episode import Episode
        return Episode.begin(self, session_id, place)

    # -- topics -------------------------------------------------------------

    def topic(self, name: str, description: str | None = None) -> int:
        """The id of a topic, creating it if it's new. A description, when
        given, replaces the old one: it describes the topic, not a memory."""
        name = topic_name(name)
        row = self.db.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()
        if row:
            if description is not None:
                self.db.execute("UPDATE topics SET description=? WHERE id=?",
                                (description.strip(), row[0]))
            return row[0]
        return self.db.execute(
            "INSERT INTO topics(name, description, created_at) VALUES (?,?,?)",
            (name, (description or "").strip(), time.time())).lastrowid

    def topics(self) -> list[dict]:
        """Every topic with its description and size. Never its contents."""
        cur = self.db.execute(
            "SELECT t.name, t.description, count(m.id) FROM topics t"
            " LEFT JOIN memory_topics mt ON mt.topic_id = t.id"
            " LEFT JOIN memories m ON m.id = mt.memory_id AND m.superseded_by IS NULL"
            " GROUP BY t.id ORDER BY t.name")
        return [{"name": n, "description": d, "memories": c} for n, d, c in cur]

    def file(self, memory_id: int, topic: str, *, source: str,
             at: float | None = None) -> None:
        """Add a memory to a topic, recorded as evidence."""
        tid = self.topic(topic)
        with self.db:
            self.db.execute("BEGIN")
            self._file(memory_id, tid, topic, source, time.time() if at is None else at, None)

    def unfile(self, memory_id: int, topic: str, *, source: str) -> None:
        """Take a memory out of a topic, recorded as evidence."""
        row = self.db.execute("SELECT id FROM topics WHERE name=?", (topic,)).fetchone()
        if row is None:
            return
        with self.db:
            self.db.execute("BEGIN")
            gone = self.db.execute("DELETE FROM memory_topics WHERE memory_id=? AND topic_id=?",
                                   (memory_id, row[0])).rowcount
            if gone:
                self._evidence(memory_id, "unfile", source, ts=time.time(), note=topic)

    def _file(self, memory_id: int, tid: int, name: str, source: str, at: float, episode) -> None:
        added = self.db.execute("INSERT OR IGNORE INTO memory_topics VALUES (?, ?)",
                                (memory_id, tid)).rowcount
        if added:
            self._evidence(memory_id, "file", source, episode=episode, ts=at, note=name)

    # -- remember -----------------------------------------------------------

    def remember(self, content: str, *, source: str, topics: Iterable[str] = (),
                 at: float | None = None, episode: str | None = None,
                 key: str | None = None, supersedes: int | None = None,
                 superseded_by_judgment_of: str | None = None,
                 volatility: str | None = None, associate: Iterable[int] = (),
                 drawn_from: Iterable[str] = (), reasoner: str | None = None) -> int:
        """Record a memory with its first evidence. Returns its id.

        `topics` files it; none means the general topic. Idempotent: the same
        `key` (by default, topics + content + time learned) returns the existing
        memory and adds nothing.

        `associate` is every memory the new one is linked to as it's born: the others
        from the same moment, and the ones it builds on or corrects. Deciding which it
        builds on is the writer's judgment; linking is fixed (docs/primitives/ASSOCIATION.md).
        `drawn_from` names the episode turns it was drawn from.

        Deciding that a new memory replaces `supersedes` is a judgment, and the
        supersede evidence names whoever made it (`superseded_by_judgment_of`),
        which isn't always the new memory's source: the user says "it's blue
        now", the agent decides that replaces "it's pink" (docs/primitives/MEMORY.md).
        """
        names = sorted({topic_name(t) for t in topics})
        if volatility is not None and volatility not in belief.HALF_LIFE:
            raise ValueError(f"unknown volatility {volatility!r}; expected one of {tuple(belief.HALF_LIFE)}")
        if not content or not content.strip():
            raise ValueError("a memory needs content")
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
        at = time.time() if at is None else at
        key = key or hashlib.sha256(f"{','.join(names)}\0{content}\0{at}".encode()).hexdigest()
        existing = (self.db.execute("SELECT id FROM memories WHERE key=?", (key,)).fetchone()
                    or self.db.execute("SELECT id FROM forgotten WHERE key=?", (key,)).fetchone())
        if existing:
            return existing[0]
        vec = self._encode([content])[0]
        topic_ids = [(self.topic(n), n) for n in names]
        links = list(dict.fromkeys(associate))
        with self.db:
            self.db.execute("BEGIN")
            # Never reuse the id of a forgotten memory: "why #N" must not find a stranger.
            nid = self.db.execute("SELECT max(coalesce((SELECT max(id) FROM memories), 0),"
                                  " coalesce((SELECT max(id) FROM forgotten), 0)) + 1").fetchone()[0]
            mid = self.db.execute(
                "INSERT INTO memories(id, content, key, created_at, embedding, embedding_model, volatility)"
                " VALUES (?,?,?,?,?,?,?)",
                (nid, content, key, at, _pack(vec), self._model_tag, volatility)).lastrowid
            self._evidence(mid, "observe", source, episode=episode, ts=at, reasoner=reasoner)
            for tid, name in topic_ids:
                self._file(mid, tid, name, source, at, episode)
            for other in links:
                if self.db.execute("SELECT 1 FROM memories WHERE id=?", (other,)).fetchone() is None:
                    raise KeyError(f"no memory {other} to associate with")
                if other != mid:
                    self.db.executemany("INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
                                        [(mid, other, BORN_STRENGTH, at), (other, mid, BORN_STRENGTH, at)])
            self.db.executemany("INSERT OR IGNORE INTO drawn_from VALUES (?,?)",
                                [(mid, k) for k in drawn_from])
            if supersedes is not None:
                self._supersede(supersedes, mid, superseded_by_judgment_of or source, at, episode)
        return mid

    def strengthen(self, in_mind: Iterable[int], *, at: float | None = None) -> int:
        """Strengthen the associations among memories that were in mind together at a moment
        that taught something new. Makes none: an association is only ever born with a fact.
        Returns how many were strengthened."""
        ids = sorted(set(in_mind))
        if len(ids) < 2:
            return 0
        at = time.time() if at is None else at
        marks = ",".join("?" * len(ids))
        with self.db:
            self.db.execute("BEGIN")
            rows = self.db.execute(f"SELECT src, dst, strength, last_used_at FROM associations"
                                   f" WHERE src IN ({marks}) AND dst IN ({marks})", [*ids, *ids]).fetchall()
            for src, dst, s, used in rows:
                s = _faded(s, used, at)
                self.db.execute("UPDATE associations SET strength=?, last_used_at=? WHERE src=? AND dst=?",
                                (s + STRENGTHEN * (1 - s), at, src, dst))
        return len(rows)

    def associations(self, memory_id: int, now: float | None = None) -> dict[int, float]:
        """Where this memory leads, and how strongly now, before any bound on what flows in."""
        now = time.time() if now is None else now
        return {dst: _faded(s, used, now) for dst, s, used in self.db.execute(
            "SELECT dst, strength, last_used_at FROM associations WHERE src=?", (memory_id,))}

    def supersede(self, old: int, new: int, *, source: str, at: float | None = None) -> None:
        with self.db:
            self.db.execute("BEGIN")
            self._supersede(old, new, source, time.time() if at is None else at, None)

    def _supersede(self, old: int, new: int, source: str, at: float, episode) -> None:
        row = self.db.execute("SELECT superseded_by FROM memories WHERE id=?", (old,)).fetchone()
        if row is None:
            raise KeyError(f"no memory {old}")
        if row[0] is not None:
            raise ValueError(f"memory {old} was already superseded by {row[0]}")
        self.db.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new, old))
        self._evidence(old, "supersede", source, episode=episode, ts=at,
                       note=f"superseded by {new}")

    def _evidence(self, memory_id: int, kind: str, source: str, *, ts: float,
                  episode=None, note=None, trigger=None, reasoner=None) -> None:
        # Every entry names a known source (docs/primitives/EVIDENCE.md).
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
        self.db.execute(
            "INSERT INTO evidence(memory_id, kind, source, episode, note, trigger, reasoner, ts)"
            " VALUES (?,?,?,?,?,?,?,?)", (memory_id, kind, source, episode, note, trigger, reasoner, ts))

    def evidence(self, memory_id: int) -> list[dict]:
        cur = self.db.execute(
            "SELECT kind, source, episode, note, ts FROM evidence WHERE memory_id=? ORDER BY ts, id",
            (memory_id,))
        return [dict(zip(("kind", "source", "episode", "note", "ts"), r)) for r in cur]

    def confirm(self, memory_id: int, *, source: str, at: float | None = None,
                episode: str | None = None, reasoner: str | None = None,
                drawn_from: Iterable[str] = ()) -> None:
        """Record that something outside the mind confirmed a memory (docs/primitives/EVIDENCE.md).
        `drawn_from` names the episode turns that said it again."""
        self._outside_evidence("confirm", memory_id, source, at, episode, reasoner, drawn_from)

    def contradict(self, memory_id: int, *, source: str, at: float | None = None,
                   episode: str | None = None) -> None:
        """Record that something outside the mind contradicted a memory. A correction that
        says what is true instead is a new memory that supersedes this one."""
        self._outside_evidence("contradict", memory_id, source, at, episode, None, ())

    def _outside_evidence(self, kind: str, memory_id: int, source: str, at, episode, reasoner,
                          drawn_from: Iterable[str]) -> None:
        if self.superseded_by(memory_id) is not None:
            raise ValueError(f"memory {memory_id} was superseded; it is no longer being checked")
        with self.db:
            self.db.execute("BEGIN")
            self._evidence(memory_id, kind, source, ts=time.time() if at is None else at,
                           episode=episode, reasoner=reasoner)
            self.db.executemany("INSERT OR IGNORE INTO drawn_from VALUES (?,?)",
                                [(memory_id, k) for k in drawn_from])

    def belief(self, memory_id: int, now: float | None = None) -> belief.Belief:
        """How much this memory is believed at `now`, read from its evidence alone."""
        row = self.db.execute("SELECT volatility FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"no memory {memory_id}")
        rows = self.db.execute("SELECT id, kind, source, episode, strength, ts FROM evidence"
                               " WHERE memory_id=?", (memory_id,))
        ev = [dict(zip(("id", "kind", "source", "episode", "strength", "ts"), r)) for r in rows]
        return belief.read(ev, row[0], time.time() if now is None else now)

    def content(self, memory_id: int) -> str:
        """What a memory says, so the owner can be told what is about to be forgotten."""
        row = self.db.execute("SELECT content FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"no memory {memory_id}")
        return row[0]

    def recorded(self, key: str) -> bool:
        """Whether a fact with this key was already recorded, even if it was since forgotten."""
        return (self.db.execute("SELECT 1 FROM memories WHERE key=?", (key,)).fetchone()
                or self.db.execute("SELECT 1 FROM forgotten WHERE key=?", (key,)).fetchone()) is not None

    def was_forgotten(self, memory_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM forgotten WHERE id=?", (memory_id,)).fetchone() is not None

    # -- forget -------------------------------------------------------------

    def forgetting(self, memory_id: int) -> Forgotten:
        """What forgetting this memory would take, without taking it."""
        if self.db.execute("SELECT 1 FROM memories WHERE id=?", (memory_id,)).fetchone() is None:
            raise KeyError(f"no memory {memory_id}")
        restored = [r[0] for r in self.db.execute(
            "SELECT id FROM memories WHERE superseded_by=?", (memory_id,))]
        return Forgotten(ids=[memory_id], restored=sorted(restored))

    def forget(self, memory_id: int) -> Forgotten:
        """Forget a memory completely, on its owner's request (docs/primitives/MEMORY.md).

        Its evidence, associations, topic filings, index entries and the turns it was drawn
        from go with it. A memory it had superseded is current again, since the correction
        no longer exists.
        """
        plan = self.forgetting(memory_id)
        gone = plan.ids
        marks = ",".join("?" * len(gone))
        rows = self.db.execute(f"SELECT id, content, key FROM memories WHERE id IN ({marks})", gone).fetchall()
        turn_keys = [r[0] for r in self.db.execute(
            f"SELECT turn_key FROM drawn_from WHERE memory_id IN ({marks})", gone)]
        with self.db:
            self.db.execute("BEGIN")
            for old in plan.restored:
                self.db.execute("UPDATE memories SET superseded_by=NULL WHERE id=?", (old,))
                self.db.execute("DELETE FROM evidence WHERE memory_id=? AND kind='supersede' AND note IN"
                                f" ({','.join('?' * len(gone))})", (old, *[f"superseded by {g}" for g in gone]))
            # A conflict a model found with a forgotten memory goes with it, and so does what it did to
            # the other one's belief.
            self.db.execute("DELETE FROM evidence WHERE kind='contradict' AND trigger LIKE 'judge:%' AND note IN"
                            f" ({','.join('?' * len(gone))})", [f"conflicts with #{g}" for g in gone])
            self.db.execute(f"DELETE FROM judgments WHERE memory_id IN ({marks}) OR other_id IN ({marks})",
                            [*gone, *gone])
            self.db.execute(f"DELETE FROM judged WHERE memory_id IN ({marks})", gone)
            self.db.execute(f"DELETE FROM associations WHERE src IN ({marks}) OR dst IN ({marks})",
                            [*gone, *gone])
            self.db.execute(f"DELETE FROM memory_topics WHERE memory_id IN ({marks})", gone)
            self.db.execute(f"DELETE FROM recalled WHERE memory_id IN ({marks})", gone)
            self.db.execute(f"DELETE FROM drawn_from WHERE memory_id IN ({marks})", gone)
            # The words it came from leave every episode, and other memories drawn from the
            # same turns no longer point at words that are gone.
            for k in turn_keys:
                self.db.execute("DELETE FROM turns WHERE key=?", (k,))
                self.db.execute("DELETE FROM drawn_from WHERE turn_key=?", (k,))
                self.db.execute("INSERT OR IGNORE INTO spent_turns VALUES (?)", (k,))
            self.db.execute(f"DELETE FROM evidence WHERE memory_id IN ({marks})", gone)
            for mid, content, _ in rows:
                self.db.execute("INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', ?, ?)",
                                (mid, content))
            self.db.execute(f"DELETE FROM memories WHERE id IN ({marks})", gone)
            now = time.time()
            self.db.executemany("INSERT INTO forgotten(id, key, at) VALUES (?,?,?)",
                                [(mid, key, now) for mid, _, key in rows])
        self.scrub()
        return plan

    def scrub(self) -> None:
        """Make deleted words unreadable on disk. The index keeps them until its segments are
        merged, and the write-ahead log keeps old pages until it is checkpointed."""
        self.db.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # -- judging ------------------------------------------------------------

    def to_judge(self, limit: int = 50) -> list[int]:
        """Current memories no model has compared with their neighbours yet, oldest first."""
        return [r[0] for r in self.db.execute(
            "SELECT m.id FROM memories m WHERE m.superseded_by IS NULL"
            " AND m.id NOT IN (SELECT memory_id FROM judged) ORDER BY m.id LIMIT ?", (limit,))]

    def neighbours(self, memory_id: int, *, prompt: str, k: int = 10,
                   min_cosine: float = 0.30) -> list[tuple[int, str]]:
        """The current memories nearest this one that haven't been compared with it yet, as (id, content)."""
        mine = self.db.execute("SELECT embedding, embedding_model FROM memories WHERE id=?",
                               (memory_id,)).fetchone()
        if mine is None:
            raise KeyError(f"no memory {memory_id}")
        rows = self.db.execute(
            "SELECT m.id, m.content, m.embedding, m.embedding_model FROM memories m WHERE m.superseded_by IS NULL"
            " AND m.id != ? AND NOT EXISTS (SELECT 1 FROM judgments j WHERE j.prompt = ? AND"
            " ((j.memory_id = m.id AND j.other_id = ?) OR (j.memory_id = ? AND j.other_id = m.id)))",
            (memory_id, prompt, memory_id, memory_id)).fetchall()
        if mine[0] is None or mine[1] != self._model_tag or any(r[2] is None or r[3] != self._model_tag for r in rows):
            raise RecallFailed(f"memories lack a {self._model_tag} embedding; run backfill before judging")
        if not rows:
            return []
        cos = np.vstack([np.frombuffer(r[2], dtype="<f4") for r in rows]) @ np.frombuffer(mine[0], dtype="<f4")
        best = [i for i in np.argsort(-cos) if cos[i] >= min_cosine][:k]
        return [(rows[i][0], rows[i][1]) for i in best]

    def record_judgments(self, memory_id: int, *, prompt: str, model: str,
                         categories: dict[int, str], at: float | None = None) -> None:
        """Keep what a model said about this memory and each neighbour, and mark the memory done.

        A conflict is evidence against both memories, from the other one's source, naming the model
        and the prompt: neither is superseded, because which one is right takes more than the
        model's say-so. Both stay current, and recall shows them together."""
        at = time.time() if at is None else at
        with self.db:
            self.db.execute("BEGIN")
            for other, category in categories.items():
                self.db.execute("INSERT OR IGNORE INTO judgments VALUES (?,?,?,?,?,?)",
                                (memory_id, other, prompt, model, category, at))
                if category != "conflict":
                    continue
                for a, b in ((memory_id, other), (other, memory_id)):
                    src = self.db.execute("SELECT source FROM evidence WHERE memory_id=? AND kind='observe'",
                                          (b,)).fetchone()[0]
                    self._evidence(a, "contradict", src, ts=at, note=f"conflicts with #{b}",
                                   trigger=f"judge:{prompt}", reasoner=model)
            self.db.execute("INSERT OR REPLACE INTO judged VALUES (?,?,?)", (memory_id, prompt, at))

    def conflicts(self, memory_id: int) -> list[int]:
        """The memories a model found in conflict with this one, which are still current."""
        ids = [int(re.fullmatch(r"conflicts with #(\d+)", r[0]).group(1)) for r in self.db.execute(
            "SELECT note FROM evidence WHERE memory_id=? AND kind='contradict' AND trigger LIKE 'judge:%'",
            (memory_id,))]
        return [i for i in dict.fromkeys(ids)
                if self.db.execute("SELECT 1 FROM memories WHERE id=? AND superseded_by IS NULL", (i,)).fetchone()]

    def superseded_by(self, memory_id: int) -> int | None:
        """The memory that replaced this one, or None if it is current. KeyError if unknown."""
        row = self.db.execute("SELECT superseded_by FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"no memory {memory_id}")
        return row[0]

    def visible(self, memory_id: int, topics: Iterable[str] = ()) -> bool:
        """Whether recall over these topics (plus the general one) could return this memory."""
        pred, params = self._visibility(topics)
        return self.db.execute(f"SELECT 1 FROM memories m WHERE m.id=? AND {pred}",
                               (memory_id, *params)).fetchone() is not None

    # -- recall -------------------------------------------------------------

    def _visibility(self, topics: Iterable[str]) -> tuple[str, list]:
        """SQL predicate on alias m: the general topic, or any of these."""
        names = list(dict.fromkeys(topics))
        ids = [r[0] for r in self.db.execute(
            f"SELECT id FROM topics WHERE name IN ({','.join('?' * len(names))})", names)] \
            if names else []
        general = "NOT EXISTS (SELECT 1 FROM memory_topics x WHERE x.memory_id = m.id)"
        if not ids:
            return general, []
        return (f"({general} OR EXISTS (SELECT 1 FROM memory_topics x WHERE"
                f" x.memory_id = m.id AND x.topic_id IN ({','.join('?' * len(ids))})))"), ids

    def recall(self, query: str, *, topics: Iterable[str] = (), k: int = 5) -> list[Recalled]:
        """What the mind holds that bears on `query`, best first, then what those bring to mind.

        Candidates are current (not superseded) memories in the general topic or in any
        of `topics`; nothing else is read, so nothing else can influence the result.
        Semantic and keyword rankings are fused by reciprocal rank; keyword can admit a
        memory the semantic floor withheld, but never changes its cosine, so the label
        stays honest. Associations from what was found are then followed, only to
        memories this lookup can see (docs/primitives/ASSOCIATION.md).
        """
        if not query.strip():
            return []
        now = time.time()
        visible, params = self._visibility(topics)
        try:
            rows = self.db.execute(
                f"SELECT m.id, m.content, m.created_at, m.embedding, m.embedding_model,"
                f" (SELECT group_concat(t.name, char(31)) FROM memory_topics x"
                f"  JOIN topics t ON t.id = x.topic_id WHERE x.memory_id = m.id)"
                f" FROM memories m WHERE m.superseded_by IS NULL AND {visible}",
                params).fetchall()
            if not rows:
                return []
            stale = [r for r in rows if r[4] != self._model_tag or r[3] is None]
            if stale:
                raise RecallFailed(f"{len(stale)} memories lack a {self._model_tag} embedding; "
                                   "run backfill before recalling")
            qv = self._encode([query])[0]
            M = np.vstack([np.frombuffer(r[3], dtype="<f4") for r in rows])
            cos = M @ qv
        except RecallFailed:
            raise
        except Exception as e:
            raise RecallFailed(f"{type(e).__name__}: {e}") from e

        order = np.argsort(-cos)
        sem_rank = {rows[i][0]: rank for rank, i in enumerate(order)
                    if cos[i] >= MATCH_WITHHOLD}
        kw_rank = {mid: rank for rank, mid in
                   enumerate(self._keyword(query, visible, params, k * 4))}
        by_id = {r[0]: (r, float(c)) for r, c in zip(rows, cos)}
        fused = {}
        for mid in set(sem_rank) | set(kw_rank):
            if mid not in by_id:
                continue
            s = ((1 / (RRF_K + 1 + sem_rank[mid]) if mid in sem_rank else 0.0)
                 + (KEYWORD_WEIGHT / (RRF_K + 1 + kw_rank[mid]) if mid in kw_rank else 0.0))
            via = ("both" if mid in sem_rank and mid in kw_rank
                   else "semantic" if mid in sem_rank else "keyword")
            fused[mid] = (s, via, None)
        best = sorted(fused.items(), key=lambda kv: (-kv[1][0], -by_id[kv[0]][1]))[:k]
        shown = dict(best)
        for mid, (_, _, by) in self._brought_to_mind(list(shown), by_id, now, limit=max(1, k // 2)).items():
            shown.setdefault(mid, (0.0, "association", by))
        # A conflict is shown whole: when a result has a conflicting partner this lookup can see,
        # the partner comes with it, so one never silently wins (docs/capabilities/RECALL.md).
        partners = {mid: [p for p in self.conflicts(mid) if p in by_id] for mid in shown}
        for ps in list(partners.values()):
            for p in ps:
                if p not in shown:
                    shown[p] = (0.0, "conflict", None)
                    partners[p] = [q for q in self.conflicts(p) if q in by_id]
        out = []
        for mid, (s, via, by) in shown.items():
            r, c = by_id[mid]
            out.append(Recalled(id=mid, content=r[1],
                                topics=tuple(sorted(r[5].split(chr(31)))) if r[5] else (),
                                score=s, cosine=c, via=via, created_at=r[2],
                                belief=self.belief(mid, now), conflicts=tuple(partners[mid]),
                                brought_by=by))
        return out

    def _brought_to_mind(self, found: list[int], by_id: dict, now: float, *, limit: int) -> dict:
        """What the associations of `found` lead to among the memories this lookup can see,
        strongest first. Strength from memories it can't see is never counted, so a topic
        that isn't searched leaves no trace here either."""
        if not found:
            return {}
        seen = set(by_id)
        inflow: dict[int, float] = {}
        edges = []
        for src, dst, s, used in self.db.execute(
                f"SELECT src, dst, strength, last_used_at FROM associations"
                f" WHERE dst IN (SELECT dst FROM associations WHERE src IN ({','.join('?' * len(found))}))",
                found):
            if src not in seen or dst not in seen:
                continue
            s = _faded(s, used, now)
            inflow[dst] = inflow.get(dst, 0.0) + s
            if src in found and dst not in found:
                edges.append((src, dst, s))
        reached: dict[int, tuple[float, str, int]] = {}
        for src, dst, s in edges:
            s = s * min(1.0, INFLOW_CAP / inflow[dst])
            if s >= FOLLOW and (dst not in reached or reached[dst][0] < s):
                reached[dst] = (s, "association", src)
        return dict(sorted(reached.items(), key=lambda kv: -kv[1][0])[:limit])

    def _keyword(self, query: str, visible: str, params: list, limit: int) -> list[int]:
        terms = [t for t in _WORD.findall(query.lower()) if t not in _STOP and len(t) > 1]
        if not terms:
            return []
        return [r[0] for r in self.db.execute(
            f"SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid"
            f" WHERE memories_fts MATCH ? AND m.superseded_by IS NULL AND {visible}"
            f" ORDER BY f.rank LIMIT ?",
            (" OR ".join(f'"{t}"' for t in terms), *params, limit))]

    def close(self) -> None:
        self.db.close()


def _faded(strength: float, last_used: float, now: float) -> float:
    """An association weakens without use, and is never severed."""
    return strength * 2 ** (-max(0.0, now - last_used) / ASSOCIATION_HALF_LIFE)
