"""Memory systems under test. Each one can be seeded from a fixture and queried.

A system reports only what it would actually serve. If it doesn't understand
scopes or supersession it ignores them, and the metrics show the cost of that.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

from bench.fixture import Memory
from bench.metrics import Hit


class System(Protocol):
    name: str

    def config(self) -> dict:
        """Everything that affects results, for the results log."""
        ...

    def seed(self, memories: list[Memory]) -> None:
        """Start from an empty mind and add these memories, in order."""
        ...

    def recall(self, text: str, *, scopes: frozenset[str], k: int,
               at: dt.datetime | None) -> list[Hit]:
        """What the system would serve for this query, best first."""
        ...

    def close(self) -> None:
        ...

    # Used by scenario runs, which put recall in front of a real agent.

    def system_prompt_block(self) -> str:
        """Standing instructions the host adds to the system prompt."""
        ...

    def render(self, hits: list[Hit], contents: dict[str, str]) -> str:
        """Recall as the agent would see it, before the host wraps it."""
        ...


def get(name: str) -> System:
    if name == "bm25":
        from bench.systems.bm25 import BM25
        return BM25()
    if name == "mnemonic":
        from bench.systems.mnemonic import Mnemonic
        return Mnemonic()
    raise KeyError(f"unknown system {name!r}; known: bm25, mnemonic")


NAMES = ("bm25", "mnemonic")
