"""A mind: one owner's memories, associations and evidence, in one SQLite file.

Stage 1 covers Remember and Recall (invariants/capabilities/REMEMBER.md,
RECALL.md). Belief lives in the schema from the start, but it isn't computed
until Stage 2, so recall reports belief as unknown rather than inventing one.

Rules this module enforces:
- Memory content is never edited. A newer memory supersedes an older one, and
  the older stays in history, never served as current.
- Every memory is born with evidence naming its source.
- Remembering the same thing twice is idempotent.
- Recall sees only loaded scopes, and an unloaded scope affects nothing.
- A recall that can't search says so. It raises; it never returns an empty
  list that looks like "nothing relevant".
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

Encode = Callable[[list[str]], np.ndarray]

SCHEMA_VERSION = 1
USER_SCOPE = "user"

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
CREATE TABLE IF NOT EXISTS scopes (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    parent  INTEGER REFERENCES scopes(id)
);
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY,
    scope_id        INTEGER NOT NULL REFERENCES scopes(id),
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
CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope_id);
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
CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY,
    memory_id   INTEGER NOT NULL REFERENCES memories(id),
    kind        TEXT NOT NULL CHECK (kind IN
                  ('observe','confirm','contradict','supersede','rescope')),
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
class Recalled:
    id: int
    content: str
    scope: str
    score: float           # fused rank score; only its order means anything
    cosine: float          # topical similarity to the query
    via: str               # "semantic", "keyword" or "both"
    created_at: float

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
    """One owner's mind. Not thread-safe; open one per thread."""

    def __init__(self, path: Path | str, *, encode: Encode, model_tag: str) -> None:
        self.path = Path(path)
        self._encode = encode
        self._model_tag = model_tag
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))
        self.db.execute("INSERT OR IGNORE INTO scopes(name, parent) VALUES (?, NULL)",
                        (USER_SCOPE,))

    # -- scopes -------------------------------------------------------------

    def scope(self, name: str, parent: str = USER_SCOPE) -> int:
        """The id of a scope, creating it under `parent` if it's new."""
        row = self.db.execute("SELECT id FROM scopes WHERE name=?", (name,)).fetchone()
        if row:
            return row[0]
        pid = None if name == USER_SCOPE else self.scope(parent)
        return self.db.execute("INSERT INTO scopes(name, parent) VALUES (?, ?)",
                               (name, pid)).lastrowid

    def scopes(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT name FROM scopes ORDER BY id")]

    # -- remember -----------------------------------------------------------

    def remember(self, content: str, *, source: str, scope: str = USER_SCOPE,
                 at: float | None = None, episode: str | None = None,
                 key: str | None = None, supersedes: int | None = None) -> int:
        """Record a memory with its first evidence. Returns its id.

        Idempotent: the same `key` (by default, scope + content + time learned)
        returns the existing memory and adds nothing.
        """
        if not content or not content.strip():
            raise ValueError("a memory needs content")
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
        at = time.time() if at is None else at
        key = key or hashlib.sha256(f"{scope}\0{content}\0{at}".encode()).hexdigest()
        existing = self.db.execute("SELECT id FROM memories WHERE key=?", (key,)).fetchone()
        if existing:
            return existing[0]
        vec = self._encode([content])[0]
        with self.db:
            self.db.execute("BEGIN")
            mid = self.db.execute(
                "INSERT INTO memories(scope_id, content, key, created_at, embedding,"
                " embedding_model) VALUES (?,?,?,?,?,?)",
                (self.scope(scope), content, key, at, _pack(vec), self._model_tag)).lastrowid
            self._evidence(mid, "observe", source, episode=episode, ts=at)
            if supersedes is not None:
                self._supersede(supersedes, mid, source, at, episode)
        return mid

    def supersede(self, old: int, new: int, *, source: str, at: float | None = None) -> None:
        with self.db:
            self.db.execute("BEGIN")
            self._supersede(old, new, source, time.time() if at is None else at, None)

    def _supersede(self, old: int, new: int, source: str, at: float, episode) -> None:
        row = self.db.execute("SELECT superseded_by FROM memories WHERE id=?", (old,)).fetchone()
        if row is None:
            raise KeyError(f"no memory {old}")
        if row[0] is None:
            self.db.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new, old))
        self._evidence(old, "supersede", source, episode=episode, ts=at,
                       note=f"superseded by {new}")

    def _evidence(self, memory_id: int, kind: str, source: str, *, ts: float,
                  episode=None, note=None) -> None:
        self.db.execute(
            "INSERT INTO evidence(memory_id, kind, source, episode, note, ts)"
            " VALUES (?,?,?,?,?,?)", (memory_id, kind, source, episode, note, ts))

    def evidence(self, memory_id: int) -> list[dict]:
        cur = self.db.execute(
            "SELECT kind, source, episode, note, ts FROM evidence WHERE memory_id=? ORDER BY ts, id",
            (memory_id,))
        return [dict(zip(("kind", "source", "episode", "note", "ts"), r)) for r in cur]

    # -- recall -------------------------------------------------------------

    def _visible(self, scopes: Iterable[str]) -> list[int]:
        names = list(dict.fromkeys(scopes))
        if not names:
            return []
        q = f"SELECT id FROM scopes WHERE name IN ({','.join('?' * len(names))})"
        return [r[0] for r in self.db.execute(q, names)]

    def recall(self, query: str, *, scopes: Iterable[str], k: int = 5) -> list[Recalled]:
        """What the mind holds that bears on `query`, best first.

        Only current (not superseded) memories in loaded scopes are candidates.
        Semantic and keyword rankings are fused by reciprocal rank; keyword can
        admit a memory the semantic floor withheld, but never changes its
        cosine, so the label stays honest.
        """
        if not query.strip():
            return []
        scope_ids = self._visible(scopes)
        if not scope_ids:
            return []
        marks = ",".join("?" * len(scope_ids))
        try:
            rows = self.db.execute(
                f"SELECT m.id, m.content, s.name, m.created_at, m.embedding, m.embedding_model"
                f" FROM memories m JOIN scopes s ON s.id = m.scope_id"
                f" WHERE m.superseded_by IS NULL AND m.scope_id IN ({marks})",
                scope_ids).fetchall()
            if not rows:
                return []
            stale = [r for r in rows if r[5] != self._model_tag or r[4] is None]
            if stale:
                raise RecallFailed(f"{len(stale)} memories lack a {self._model_tag} embedding; "
                                   "run backfill before recalling")
            qv = self._encode([query])[0]
            M = np.vstack([np.frombuffer(r[4], dtype="<f4") for r in rows])
            cos = M @ qv
        except RecallFailed:
            raise
        except Exception as e:
            raise RecallFailed(f"{type(e).__name__}: {e}") from e

        order = np.argsort(-cos)
        sem_rank = {rows[i][0]: rank for rank, i in enumerate(order)
                    if cos[i] >= MATCH_WITHHOLD}
        kw_rank = {mid: rank for rank, mid in enumerate(self._keyword(query, scope_ids, k * 4))}
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
        out = []
        for mid, (s, via) in best:
            r, c = by_id[mid]
            out.append(Recalled(id=mid, content=r[1], scope=r[2], score=s, cosine=c,
                                via=via, created_at=r[3]))
        return out

    def _keyword(self, query: str, scope_ids: list[int], limit: int) -> list[int]:
        terms = [t for t in _WORD.findall(query.lower()) if t not in _STOP and len(t) > 1]
        if not terms:
            return []
        marks = ",".join("?" * len(scope_ids))
        return [r[0] for r in self.db.execute(
            f"SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid"
            f" WHERE memories_fts MATCH ? AND m.superseded_by IS NULL"
            f" AND m.scope_id IN ({marks}) ORDER BY f.rank LIMIT ?",
            (" OR ".join(f'"{t}"' for t in terms), *scope_ids, limit))]

    def close(self) -> None:
        self.db.close()
