"""A mind: one owner's memories, associations and evidence, in one SQLite file.

Remember and Recall (docs/primitives/MEMORY.md, docs/capabilities/RECALL.md).
Belief is read from a memory's evidence (tapestry.belief) and never stored.

Rules this module enforces:
- Memory content is never edited. A newer memory supersedes an older one, and
  the older stays in history, never served as current.
- Every memory is born with evidence naming its source.
- Remembering the same thing twice is idempotent.
- Recall sees the default namespace plus the namespaces it's given, and a
  namespace it isn't given affects nothing.
- Forgetting is complete: the memory, its evidence, its associations, its place in
  the full-text index and anything inferred from it alone go, and nothing left
  behind says what it held. The mind remembers only that something was forgotten.
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

SCHEMA_VERSION = 2

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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- A memory in no namespace lives in the default namespace, which is always searched.
CREATE TABLE IF NOT EXISTS namespaces (
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
    volatility      TEXT,                      -- Stage 2
    belief_L        REAL,                      -- Stage 2: cached log-odds
    belief_t        REAL                       -- Stage 2: time of last evidence
);
CREATE TABLE IF NOT EXISTS memory_namespaces (
    memory_id    INTEGER NOT NULL REFERENCES memories(id),
    namespace_id INTEGER NOT NULL REFERENCES namespaces(id),
    PRIMARY KEY (memory_id, namespace_id)
);
CREATE INDEX IF NOT EXISTS memory_namespaces_ns ON memory_namespaces(namespace_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(content, content=memories, content_rowid=id);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TABLE IF NOT EXISTS associations (
    src          INTEGER NOT NULL REFERENCES memories(id),
    dst          INTEGER NOT NULL REFERENCES memories(id),
    strength     REAL NOT NULL CHECK (strength BETWEEN 0 AND 1),
    last_used_at REAL,
    PRIMARY KEY (src, dst)
);
-- Which memories a memory was inferred from (docs/primitives/MEMORY.md): forgetting
-- the last of them takes the inference with it.
CREATE TABLE IF NOT EXISTS derivations (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    from_id   INTEGER NOT NULL REFERENCES memories(id),
    PRIMARY KEY (memory_id, from_id)
);
-- What a forgotten memory leaves: that it existed, never what it held. The key stops
-- the same moment being recorded again (a transcript is ingested more than once).
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
-- Content is never edited and evidence is never rewritten.
CREATE TRIGGER IF NOT EXISTS memories_no_edit BEFORE UPDATE OF content ON memories
BEGIN SELECT RAISE(ABORT, 'memory content is never edited; supersede it'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_edit BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is never edited'); END;
"""

SOURCES = ("user", "user_confirmed", "tool", "web", "agent", "inference", "host_memory")

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
    ids: list[int]        # the memory and whatever was inferred from it alone
    restored: list[int] = field(default_factory=list)  # superseded by a forgotten memory, current again


@dataclass(frozen=True)
class Recalled:
    id: int
    content: str
    namespaces: tuple[str, ...]   # empty: the default namespace
    score: float           # fused rank score; only its order means anything
    cosine: float          # topical similarity to the query
    via: str               # "semantic", "keyword" or "both"
    created_at: float
    belief: belief.Belief | None = None   # how much it is believed; a different question from match
    conflicts: tuple[int, ...] = ()       # current memories a model found in conflict with it

    @property
    def match(self) -> str:
        if self.via == "keyword" and self.cosine < MATCH_WITHHOLD:
            return "keyword only"
        if self.cosine >= MATCH_OK:
            return "ok"
        return "LOW"


def _pack(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype="<f4").tobytes()


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
        if self.db.execute("SELECT name FROM sqlite_master WHERE name='scopes'").fetchone():
            raise RuntimeError(f"{self.path} uses the pre-namespace development schema (v1); "
                               "it can't be opened by this version")
        self.db.executescript(_SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))

    # -- namespaces ---------------------------------------------------------

    def namespace(self, name: str, description: str | None = None) -> int:
        """The id of a namespace, creating it if it's new. A description, when
        given, replaces the old one: it describes the namespace, not a memory."""
        name = name.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
            raise ValueError(f"namespace names are lowercase letters, digits, '.', '_' or '-': {name!r}")
        row = self.db.execute("SELECT id FROM namespaces WHERE name=?", (name,)).fetchone()
        if row:
            if description is not None:
                self.db.execute("UPDATE namespaces SET description=? WHERE id=?",
                                (description.strip(), row[0]))
            return row[0]
        return self.db.execute(
            "INSERT INTO namespaces(name, description, created_at) VALUES (?,?,?)",
            (name, (description or "").strip(), time.time())).lastrowid

    def namespaces(self) -> list[dict]:
        """Every namespace with its description and size. Never its contents."""
        cur = self.db.execute(
            "SELECT n.name, n.description, count(m.id) FROM namespaces n"
            " LEFT JOIN memory_namespaces mn ON mn.namespace_id = n.id"
            " LEFT JOIN memories m ON m.id = mn.memory_id AND m.superseded_by IS NULL"
            " GROUP BY n.id ORDER BY n.name")
        return [{"name": n, "description": d, "memories": c} for n, d, c in cur]

    def file(self, memory_id: int, namespace: str, *, source: str,
             at: float | None = None) -> None:
        """Add a memory to a namespace, recorded as evidence."""
        nid = self.namespace(namespace)
        with self.db:
            self.db.execute("BEGIN")
            self._file(memory_id, nid, namespace, source, time.time() if at is None else at, None)

    def unfile(self, memory_id: int, namespace: str, *, source: str) -> None:
        """Take a memory out of a namespace, recorded as evidence."""
        row = self.db.execute("SELECT id FROM namespaces WHERE name=?", (namespace,)).fetchone()
        if row is None:
            return
        with self.db:
            self.db.execute("BEGIN")
            gone = self.db.execute("DELETE FROM memory_namespaces WHERE memory_id=? AND namespace_id=?",
                                   (memory_id, row[0])).rowcount
            if gone:
                self._evidence(memory_id, "unfile", source, ts=time.time(), note=namespace)

    def _file(self, memory_id: int, nid: int, name: str, source: str, at: float, episode) -> None:
        added = self.db.execute("INSERT OR IGNORE INTO memory_namespaces VALUES (?, ?)",
                                (memory_id, nid)).rowcount
        if added:
            self._evidence(memory_id, "file", source, episode=episode, ts=at, note=name)

    # -- remember -----------------------------------------------------------

    def remember(self, content: str, *, source: str, namespaces: Iterable[str] = (),
                 at: float | None = None, episode: str | None = None,
                 key: str | None = None, supersedes: int | None = None,
                 superseded_by_judgment_of: str | None = None,
                 volatility: str | None = None) -> int:
        """Record a memory with its first evidence. Returns its id.

        `namespaces` files it; none means the default namespace. Idempotent:
        the same `key` (by default, namespaces + content + time learned)
        returns the existing memory and adds nothing.

        Deciding that a new memory replaces `supersedes` is a judgment, and the
        supersede evidence names whoever made it (`superseded_by_judgment_of`),
        which isn't always the new memory's source: the user says "it's blue
        now", the agent decides that replaces "it's pink" (docs/primitives/MEMORY.md).
        """
        names = sorted(set(namespaces))
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
        ns_ids = [(self.namespace(n), n) for n in names]
        with self.db:
            self.db.execute("BEGIN")
            # Never reuse the id of a forgotten memory: "why #N" must not find a stranger.
            nid = self.db.execute("SELECT max(coalesce((SELECT max(id) FROM memories), 0),"
                                  " coalesce((SELECT max(id) FROM forgotten), 0)) + 1").fetchone()[0]
            mid = self.db.execute(
                "INSERT INTO memories(id, content, key, created_at, embedding, embedding_model, volatility)"
                " VALUES (?,?,?,?,?,?,?)",
                (nid, content, key, at, _pack(vec), self._model_tag, volatility)).lastrowid
            self._evidence(mid, "observe", source, episode=episode, ts=at)
            for nid, name in ns_ids:
                self._file(mid, nid, name, source, at, episode)
            if supersedes is not None:
                self._supersede(supersedes, mid, superseded_by_judgment_of or source, at, episode)
        return mid

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
                episode: str | None = None) -> None:
        """Record that something outside the mind confirmed a memory (docs/primitives/EVIDENCE.md)."""
        self._outside_evidence("confirm", memory_id, source, at, episode)

    def contradict(self, memory_id: int, *, source: str, at: float | None = None,
                   episode: str | None = None) -> None:
        """Record that something outside the mind contradicted a memory. A correction that
        says what is true instead is a new memory that supersedes this one."""
        self._outside_evidence("contradict", memory_id, source, at, episode)

    def _outside_evidence(self, kind: str, memory_id: int, source: str, at, episode) -> None:
        if self.superseded_by(memory_id) is not None:
            raise ValueError(f"memory {memory_id} was superseded; it is no longer being checked")
        with self.db:
            self.db.execute("BEGIN")
            self._evidence(memory_id, kind, source, ts=time.time() if at is None else at, episode=episode)

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
        """Whether a moment with this key was already recorded, even if it was since forgotten."""
        return (self.db.execute("SELECT 1 FROM memories WHERE key=?", (key,)).fetchone()
                or self.db.execute("SELECT 1 FROM forgotten WHERE key=?", (key,)).fetchone()) is not None

    def was_forgotten(self, memory_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM forgotten WHERE id=?", (memory_id,)).fetchone() is not None

    # -- forget -------------------------------------------------------------

    def forgetting(self, memory_id: int) -> Forgotten:
        """What forgetting this memory would take, without taking it."""
        if self.db.execute("SELECT 1 FROM memories WHERE id=?", (memory_id,)).fetchone() is None:
            raise KeyError(f"no memory {memory_id}")
        sources: dict[int, set[int]] = {}
        for m, f in self.db.execute("SELECT memory_id, from_id FROM derivations"):
            sources.setdefault(m, set()).add(f)
        gone = {memory_id}
        while True:  # an inference goes when every memory it came from has gone
            more = {m for m, fs in sources.items() if m not in gone and fs <= gone}
            if not more:
                break
            gone |= more
        marks = ",".join("?" * len(gone))
        restored = [r[0] for r in self.db.execute(
            f"SELECT id FROM memories WHERE superseded_by IN ({marks}) AND id NOT IN ({marks})",
            [*gone, *gone])]
        return Forgotten(ids=sorted(gone), restored=sorted(restored))

    def forget(self, memory_id: int) -> Forgotten:
        """Forget a memory completely, on its owner's request (docs/primitives/MEMORY.md).

        Its evidence, associations, namespace filings, index entries and anything inferred
        from it alone go with it. A memory it had superseded is current again, since the
        correction no longer exists.
        """
        plan = self.forgetting(memory_id)
        gone = plan.ids
        marks = ",".join("?" * len(gone))
        rows = self.db.execute(f"SELECT id, content, key FROM memories WHERE id IN ({marks})", gone).fetchall()
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
            self.db.execute(f"DELETE FROM derivations WHERE memory_id IN ({marks}) OR from_id IN ({marks})",
                            [*gone, *gone])
            self.db.execute(f"DELETE FROM associations WHERE src IN ({marks}) OR dst IN ({marks})",
                            [*gone, *gone])
            self.db.execute(f"DELETE FROM memory_namespaces WHERE memory_id IN ({marks})", gone)
            self.db.execute(f"DELETE FROM evidence WHERE memory_id IN ({marks})", gone)
            for mid, content, _ in rows:
                self.db.execute("INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', ?, ?)",
                                (mid, content))
            self.db.execute(f"DELETE FROM memories WHERE id IN ({marks})", gone)
            now = time.time()
            self.db.executemany("INSERT INTO forgotten(id, key, at) VALUES (?,?,?)",
                                [(mid, key, now) for mid, _, key in rows])
        # The index keeps a forgotten memory's words until its segments are merged, and the
        # write-ahead log keeps its old pages until it is checkpointed.
        self.db.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return plan

    # -- judging ------------------------------------------------------------

    _SETTLED = "m.superseded_by IS NULL AND NOT EXISTS (SELECT 1 FROM memory_namespaces x JOIN namespaces n" \
               " ON n.id = x.namespace_id WHERE x.memory_id = m.id AND n.name LIKE 'session.%')"

    def to_judge(self, limit: int = 50) -> list[int]:
        """Memories no model has compared with their neighbours yet, oldest first. Only lasting
        memories are compared: a turn of a conversation is a record of what was said, not a claim
        that two of them can contradict (docs/primitives/NAMESPACE.md)."""
        return [r[0] for r in self.db.execute(
            f"SELECT m.id FROM memories m WHERE {self._SETTLED}"
            " AND m.id NOT IN (SELECT memory_id FROM judged) ORDER BY m.id LIMIT ?", (limit,))]

    def neighbours(self, memory_id: int, *, prompt: str, k: int = 10,
                   min_cosine: float = 0.30) -> list[tuple[int, str]]:
        """The lasting memories nearest this one that haven't been compared with it yet, as (id, content)."""
        mine = self.db.execute("SELECT embedding, embedding_model FROM memories WHERE id=?",
                               (memory_id,)).fetchone()
        if mine is None:
            raise KeyError(f"no memory {memory_id}")
        rows = self.db.execute(
            f"SELECT m.id, m.content, m.embedding, m.embedding_model FROM memories m WHERE {self._SETTLED}"
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

    def visible(self, memory_id: int, namespaces: Iterable[str] = ()) -> bool:
        """Whether recall over these namespaces (plus the default) could return this memory."""
        pred, params = self._visibility(namespaces)
        return self.db.execute(f"SELECT 1 FROM memories m WHERE m.id=? AND {pred}",
                               (memory_id, *params)).fetchone() is not None

    # -- recall -------------------------------------------------------------

    def _visibility(self, namespaces: Iterable[str]) -> tuple[str, list]:
        """SQL predicate on alias m: the default namespace, or any of these."""
        names = list(dict.fromkeys(namespaces))
        ids = [r[0] for r in self.db.execute(
            f"SELECT id FROM namespaces WHERE name IN ({','.join('?' * len(names))})", names)] \
            if names else []
        default = "NOT EXISTS (SELECT 1 FROM memory_namespaces x WHERE x.memory_id = m.id)"
        if not ids:
            return default, []
        return (f"({default} OR EXISTS (SELECT 1 FROM memory_namespaces x WHERE"
                f" x.memory_id = m.id AND x.namespace_id IN ({','.join('?' * len(ids))})))"), ids

    def recall(self, query: str, *, namespaces: Iterable[str] = (), k: int = 5) -> list[Recalled]:
        """What the mind holds that bears on `query`, best first.

        Candidates are current (not superseded) memories in the default
        namespace or in any of `namespaces`; nothing else is read, so nothing
        else can influence the result. Semantic and keyword rankings are fused
        by reciprocal rank; keyword can admit a memory the semantic floor
        withheld, but never changes its cosine, so the label stays honest.
        """
        if not query.strip():
            return []
        now = time.time()
        visible, params = self._visibility(namespaces)
        try:
            rows = self.db.execute(
                f"SELECT m.id, m.content, m.created_at, m.embedding, m.embedding_model,"
                f" (SELECT group_concat(n.name, char(31)) FROM memory_namespaces x"
                f"  JOIN namespaces n ON n.id = x.namespace_id WHERE x.memory_id = m.id)"
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
            fused[mid] = (s, via)
        best = sorted(fused.items(), key=lambda kv: (-kv[1][0], -by_id[kv[0]][1]))[:k]
        # A conflict is shown whole: when a result has a conflicting partner this lookup can see,
        # the partner comes with it, so one never silently wins (docs/capabilities/RECALL.md).
        shown = {mid: (s, via) for mid, (s, via) in best}
        partners = {mid: [p for p in self.conflicts(mid) if p in by_id] for mid in shown}
        for ps in list(partners.values()):
            for p in ps:
                if p not in shown:
                    shown[p] = (0.0, "conflict")
                    partners[p] = [q for q in self.conflicts(p) if q in by_id]
        out = []
        for mid, (s, via) in shown.items():
            r, c = by_id[mid]
            out.append(Recalled(id=mid, content=r[1],
                                namespaces=tuple(sorted(r[5].split(chr(31)))) if r[5] else (),
                                score=s, cosine=c, via=via, created_at=r[2],
                                belief=self.belief(mid, now), conflicts=tuple(partners[mid])))
        return out

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
