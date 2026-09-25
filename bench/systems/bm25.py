"""Plain keyword search: the control every memory system should beat.

SQLite FTS5 with bm25 ranking, stopwords removed, terms OR-ed. It ignores
scopes and supersession on purpose. MemoryAgentBench found BM25 hard to beat on
conflicting facts, so it earns its place as a baseline.
"""

from __future__ import annotations

import re
import sqlite3

from bench.fixture import Memory
from bench.metrics import Hit

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be been being by can did do does for from had has have "
    "how i if in is it its me my no not of on or our out so than that the their "
    "them then there these they this those to too was we were what when where "
    "which while who why will with would you your about".split())


class BM25:
    name = "bm25"

    def __init__(self) -> None:
        self._db: sqlite3.Connection | None = None
        self._ids: list[str] = []

    def config(self) -> dict:
        return {"ranking": "fts5-bm25", "scopes": "ignored", "supersession": "ignored"}

    def seed(self, memories: list[Memory]) -> None:
        self.close()
        self._db = sqlite3.connect(":memory:")
        self._db.execute("CREATE VIRTUAL TABLE m USING fts5(content, tags)")
        self._ids = []
        for mem in memories:
            self._db.execute("INSERT INTO m(rowid, content, tags) VALUES (?,?,?)",
                             (len(self._ids) + 1, mem.content, mem.tags))
            self._ids.append(mem.id)

    def recall(self, text, *, scopes, k, at):
        terms = [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]
        if not terms:
            return []
        rows = self._db.execute(
            "SELECT rowid, bm25(m) FROM m WHERE m MATCH ? ORDER BY bm25(m) LIMIT ?",
            (" OR ".join(f'"{t}"' for t in terms), k)).fetchall()
        return [Hit(id=self._ids[rid - 1], score=-s) for rid, s in rows]

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
