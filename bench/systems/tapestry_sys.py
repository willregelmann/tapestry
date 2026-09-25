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
from tapestry.mind import MATCH_OK, MATCH_WITHHOLD, Mind

SYSTEM_PROMPT = (
    "Recalled memories arrive with labels. [match: ok] means the memory is on "
    "the topic of the message; [match: LOW] means it's only loosely related and "
    "may not answer anything; [match: keyword only] means it shares words, not "
    "meaning. Match says nothing about whether a memory is true or current. "
    "'learned' is when you learned it; the older a memory about something that "
    "changes, the more it's worth checking before relying on it for anything "
    "that matters. Say what a memory does and doesn't establish rather than "
    "asserting it.")


def _ts(at: dt.datetime | None) -> float:
    return at.timestamp() if at else time.time()


def render_one(content: str, match: str, cosine: float, learned: float) -> str:
    when = dt.datetime.fromtimestamp(learned, dt.timezone.utc).strftime("%b %Y")
    return f"[match: {match} · cos {cosine:.2f} · learned {when}] {content}"


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
        return SYSTEM_PROMPT

    def render(self, hits, contents):
        return "\n".join(render_one(contents[h.id], self._hits[h.id].match,
                                    self._hits[h.id].cosine, self._hits[h.id].created_at)
                         for h in hits)

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
