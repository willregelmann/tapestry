"""Tapestry itself, as a system under test.

Each seed opens a fresh throwaway mind. Fixture memories keep their scopes,
their learned-at times and their supersession links, because a fixture is a
record of what the mind was told and when.
"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import time
from pathlib import Path

from bench.fixture import Memory
from bench.metrics import Hit
from bench.systems.embed_cache import EmbedCache
from tapestry import embed
from tapestry import render
from tapestry.mind import MATCH_OK, MATCH_WITHHOLD, Mind

def _ts(at: dt.datetime | None) -> float:
    return at.timestamp() if at else time.time()


class Tapestry:
    name = "tapestry"

    def __init__(self) -> None:
        self._enc = embed.Encoder()
        self._cache = EmbedCache(embed.MODEL_TAG)
        self._tmp: Path | None = None
        self._mind: Mind | None = None
        self._ids: dict[int, str] = {}
        self._hits: dict[str, object] = {}

    def config(self) -> dict:
        return {"model": embed.MODEL_TAG, "match_withhold": MATCH_WITHHOLD,
                "match_ok": MATCH_OK, "fusion": "rrf", "stage": 1}

    def _encode(self, texts):
        return self._cache.encode(texts, self._enc)

    def seed(self, memories: list[Memory]) -> None:
        self._reset()
        self._tmp = Path(tempfile.mkdtemp(prefix="tapestry-bench-"))
        self._mind = Mind(self._tmp / "mind.db", encode=self._encode, model_tag=embed.MODEL_TAG)
        by_fixture: dict[str, int] = {}
        # Oldest first, so a superseding memory always finds what it replaces.
        for mem in sorted(memories, key=lambda m: _ts(m.at) if m.at else 0.0):
            if mem.scope != "user":
                self._mind.scope(mem.scope)
            mid = self._mind.remember(
                mem.content, source="user", scope=mem.scope, at=_ts(mem.at), key=mem.id,
                supersedes=by_fixture.get(mem.supersedes) if mem.supersedes else None)
            by_fixture[mem.id] = mid
            self._ids[mid] = mem.id
        # A supersedes link to a memory seeded later still has to hold.
        for mem in memories:
            if mem.supersedes and by_fixture[mem.supersedes] in self._ids:
                old = by_fixture[mem.supersedes]
                if self._mind.db.execute("SELECT superseded_by FROM memories WHERE id=?",
                                         (old,)).fetchone()[0] is None:
                    self._mind.supersede(old, by_fixture[mem.id], source="user")

    def recall(self, text, *, scopes, k, at):
        hits = self._mind.recall(text, scopes=scopes, k=k)
        self._hits = {self._ids[h.id]: h for h in hits}
        return [Hit(id=self._ids[h.id], score=h.score, label=h.match,
                    confident=h.match == "ok") for h in hits]

    def system_prompt_block(self) -> str:
        return render.GUIDE

    def render(self, hits, contents):
        # Fixture ids stand in for mind ids, so the agent never sees internal numbering.
        return render.block([self._hits[h.id] for h in hits], with_id=False)

    def _reset(self) -> None:
        if self._mind:
            self._mind.close()
            self._mind = None
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
        self._ids = {}

    def close(self) -> None:
        self._reset()
        self._cache.close()
