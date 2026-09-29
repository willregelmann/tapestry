"""Fixture format: a seeded mind plus queries with known answers.

Fixtures are TOML so they stay readable and diffable:

    name = "supersession"
    description = "Memories that change over time."

    [[memory]]
    id = "color-pink"
    namespaces = ["sam"]      # omitted: the default namespace
    content = "Sam's favorite color is pink."
    at = 2025-01-10           # when it was learned (optional)

    [[memory]]
    id = "color-blue"
    content = "Sam's favorite color is blue now."
    supersedes = "color-pink"

    [[query]]
    text = "What is Sam's favorite color?"
    expect = ["color-blue"]   # empty = nothing relevant should come back
    namespaces = ["sam"]      # searched besides the default; omitted = every namespace
    forbid = []               # extra ids that must never be served

Stale ids for a query are derived, not declared: any memory superseded by one
of the query's expected memories is stale for that query.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Memory:
    id: str
    content: str
    namespaces: tuple[str, ...] = ()   # empty: the default namespace
    at: dt.datetime | None = None
    supersedes: str | None = None
    tags: str = ""


@dataclass(frozen=True)
class Query:
    text: str
    expect: tuple[str, ...] = ()
    namespaces: frozenset[str] | None = None   # None: search every namespace
    forbid: tuple[str, ...] = ()
    at: dt.datetime | None = None
    note: str = ""

    @property
    def is_negative(self) -> bool:
        return not self.expect


@dataclass
class Fixture:
    name: str
    memories: list[Memory]
    queries: list[Query]
    description: str = ""
    path: Path | None = None
    namespace_descriptions: dict[str, str] = field(default_factory=dict)
    _by_id: dict[str, Memory] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {m.id: m for m in self.memories}
        self.validate()

    def memory(self, mid: str) -> Memory:
        return self._by_id[mid]

    @property
    def namespaces(self) -> frozenset[str]:
        return frozenset(n for m in self.memories for n in m.namespaces)

    def searched(self, q: Query) -> frozenset[str]:
        """Namespaces a query searches besides the default."""
        return q.namespaces if q.namespaces is not None else self.namespaces

    def visible(self, mid: str, q: Query) -> bool:
        ns = self._by_id[mid].namespaces
        return not ns or bool(set(ns) & self.searched(q))

    def stale_ids(self, q: Query) -> frozenset[str]:
        """Memories superseded, directly or transitively, by an expected one."""
        stale: set[str] = set()
        frontier = [self._by_id[e].supersedes for e in q.expect]
        while frontier:
            mid = frontier.pop()
            if mid and mid not in stale:
                stale.add(mid)
                frontier.append(self._by_id[mid].supersedes)
        return frozenset(stale)

    def validate(self) -> None:
        if len(self._by_id) != len(self.memories):
            dupes = sorted({m.id for m in self.memories
                            if sum(x.id == m.id for x in self.memories) > 1})
            raise ValueError(f"{self.name}: duplicate memory ids {dupes}")
        for m in self.memories:
            if m.supersedes and m.supersedes not in self._by_id:
                raise ValueError(f"{self.name}: {m.id} supersedes unknown {m.supersedes}")
        for q in self.queries:
            for mid in (*q.expect, *q.forbid):
                if mid not in self._by_id:
                    raise ValueError(f"{self.name}: query {q.text!r} names unknown {mid}")
            if q.namespaces is not None:
                unknown = q.namespaces - self.namespaces
                if unknown:
                    raise ValueError(f"{self.name}: query {q.text!r} searches unknown "
                                     f"namespaces {sorted(unknown)}")
                for mid in q.expect:
                    if not self.visible(mid, q):
                        raise ValueError(f"{self.name}: query {q.text!r} expects {mid} "
                                         f"from a namespace it doesn't search")


def _when(v) -> dt.datetime | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day, tzinfo=dt.timezone.utc)
    raise TypeError(f"expected a TOML date or datetime, got {v!r}")


def _names(m: dict) -> tuple[str, ...]:
    if "namespace" in m:
        return (m["namespace"],)
    return tuple(m.get("namespaces", ()))


def load(path: Path) -> Fixture:
    raw = tomllib.loads(Path(path).read_text())
    memories = [Memory(id=m["id"], content=m["content"], namespaces=_names(m),
                       at=_when(m.get("at")), supersedes=m.get("supersedes"),
                       tags=m.get("tags", ""))
                for m in raw.get("memory", [])]
    queries = [Query(text=q["text"], expect=tuple(q.get("expect", ())),
                     namespaces=frozenset(q["namespaces"]) if "namespaces" in q else None,
                     forbid=tuple(q.get("forbid", ())), at=_when(q.get("at")),
                     note=q.get("note", ""))
               for q in raw.get("query", [])]
    return Fixture(name=raw.get("name", Path(path).stem), memories=memories,
                   queries=queries, description=raw.get("description", ""), path=Path(path),
                   namespace_descriptions=descriptions(raw))


def descriptions(raw: dict) -> dict[str, str]:
    """[[namespace]] tables: name and description, for systems that keep them."""
    return {n["name"]: n.get("description", "") for n in raw.get("namespace", [])}


def load_dir(directory: Path) -> list[Fixture]:
    return [load(p) for p in sorted(Path(directory).glob("*.toml"))]
