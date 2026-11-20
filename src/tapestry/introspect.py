"""Introspect: the mind looked at as a whole (docs/capabilities/INTROSPECT.md).

Everything here is counted, never judged: no model is asked anything, and nothing is changed.
It finds where judgment is worth spending, such as two topics that hold much the same, a memory
filed apart from everything it resembles, or two memories that say one thing, and leaves the
judgment to whoever asked. What is done about any of it goes through the mind's ordinary acts.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

import numpy as np

from tapestry import belief, working
from tapestry.mind import GENERAL, within

if TYPE_CHECKING:
    from tapestry.mind import Mind

SAME = 0.9        # this close in meaning, two memories very likely say one thing
RELATED = 0.75    # this close, they are about the same thing and worth a look together
RESEMBLES = 0.5   # below this a neighbour says nothing about where a memory belongs
NEAR = 5          # neighbours looked at for each memory
AGREE = 4         # of those, how many must sit in one other topic to call the memory filed apart
CROWDED = 100     # recall's fixed bar is loose by this many: a random memory clears most of it (PLAN.md, C)
SMALL = 3
TYPICAL = 6       # memories nearest a topic's centre: enough to say what it holds
DOUBTFUL = 0.4    # belief's own word for it starts here
LONG = 500        # characters past which a memory is probably several claims
IDLE = 14 * belief.DAY
SHOWN = 160       # characters of a memory shown: enough to recognise it, not to read it
SECTIONS = ("topics", "memories", "web", "working")


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"


def _top(name: str) -> str:
    return name.split("/")[0]


class _Whole:
    """Every current memory, where it is filed, and the few it most resembles."""

    def __init__(self, mind: "Mind") -> None:
        rows = mind.db.execute("SELECT id, content, created_at, volatility, embedding FROM memories"
                               " WHERE superseded_by IS NULL AND embedding IS NOT NULL ORDER BY id").fetchall()
        self.ids = [r[0] for r in rows]
        self.at = {i: n for n, i in enumerate(self.ids)}
        self.text = {r[0]: r[1] for r in rows}
        self.vectors = (np.vstack([np.frombuffer(r[4], dtype="<f4") for r in rows])
                        if rows else np.zeros((0, 1), dtype="<f4"))
        self.filed: dict[int, list[str]] = defaultdict(list)
        for mid, name in mind.db.execute("SELECT x.memory_id, t.name FROM memory_topics x"
                                         " JOIN topics t ON t.id = x.topic_id ORDER BY t.name"):
            if mid in self.at:
                self.filed[mid].append(name)
        self.episode = {mid: ep for mid, ep in mind.db.execute(
            "SELECT memory_id, episode FROM evidence WHERE kind='observe'") if mid in self.at}
        self.linked: dict[int, set[int]] = defaultdict(set)
        for a, b in mind.db.execute("SELECT src, dst FROM associations"):
            if a in self.at and b in self.at:
                self.linked[a].add(b)
                self.linked[b].add(a)
        self.near: dict[int, list[tuple[int, float]]] = {}
        for start in range(0, len(self.ids), 512):
            cos = self.vectors[start:start + 512] @ self.vectors.T
            for row, mid in zip(cos, self.ids[start:start + 512]):
                row[self.at[mid]] = -1.0
                best = np.argsort(-row)[:NEAR]
                self.near[mid] = [(self.ids[j], float(row[j])) for j in best]

    def topics_of(self, mid: int) -> list[str]:
        return self.filed.get(mid) or [GENERAL]

    def inside(self, mid: int, topic: str | None) -> bool:
        return topic is None or any(within(n, topic) for n in self.topics_of(mid))

    def shown(self, mid: int) -> dict:
        return {"id": mid, "in": self.topics_of(mid), "says": _cut(self.text[mid])}


def _topics(mind: "Mind", w: _Whole, topic: str | None) -> dict:
    every = mind.topics()
    own: dict[str, list[int]] = defaultdict(list)
    for mid in w.ids:
        for name in w.topics_of(mid):
            own[name].append(mid)
    held = {t["name"]: [m for n, ids in own.items() if within(n, t["name"]) for m in ids] for t in every}
    centre = {}
    for name, ids in held.items():
        if ids:
            v = w.vectors[[w.at[i] for i in ids]].mean(axis=0)
            centre[name] = v / (np.linalg.norm(v) or 1.0)

    def closest(name: str) -> tuple[str, float] | None:
        others = [(float(centre[name] @ v), n) for n, v in centre.items()
                  if not within(n, name) and not within(name, n)]
        return (max(others)[1], round(max(others)[0], 2)) if others else None

    small, crowded = [], []
    for t in every:
        name, n = t["name"], len(set(held[t["name"]]))
        if 0 < n <= SMALL and (c := closest(name)):
            small.append({"topic": name, "holds": n, "closest": c[0], "cosine": c[1]})
        if n >= CROWDED:
            crowded.append({"topic": name, "holds": n, "under_it": sum(1 for o in every if o["name"] != name
                                                                       and within(o["name"], name))})
    alike = sorted(((round(float(centre[a] @ centre[b]), 2), a, b) for a in centre for b in centre
                    if a < b and not within(a, b) and not within(b, a)
                    and float(centre[a] @ centre[b]) >= RELATED), reverse=True)
    apart = []
    for mid in w.ids:
        mine = w.topics_of(mid)
        votes = Counter(n for other, cos in w.near[mid] if cos >= RESEMBLES
                        for n in w.topics_of(other) if n != GENERAL)
        if not votes:
            continue
        name, count = votes.most_common(1)[0]
        # Filed in the topic above is still apart: it isn't seen from the one its neighbours are in.
        if count >= AGREE and name not in mine and not any(within(m, name) for m in mine):
            apart.append({**w.shown(mid), "resembles": name})
    if topic is not None:
        if topic not in held:
            raise ValueError(f"no topic {topic!r}")
        ids = sorted(set(held[topic]))
        order = np.argsort(-(w.vectors[[w.at[i] for i in ids]] @ centre[topic])) if ids else []
        return {"description": next(t["description"] for t in every if t["name"] == topic), "holds": len(ids),
                "typical": [w.shown(ids[j]) for j in order[:TYPICAL]],
                "closest": closest(topic) if ids else None,
                "filed_apart_from_what_it_resembles": [a for a in apart if a["resembles"] == topic
                                                       or any(within(n, topic) for n in a["in"])]}
    return {
        "empty": [t["name"] for t in every if not held[t["name"]]],
        "few_memories": sorted(small, key=lambda s: -s["cosine"]),
        "crowded": sorted(crowded, key=lambda c: -c["holds"]),
        "hold_much_the_same": [{"topics": [a, b], "cosine": c} for c, a, b in alike],
        "general_holds": len(own.get(GENERAL, [])),
        "filed_apart_from_what_it_resembles": apart,
    }


def _memories(mind: "Mind", w: _Whole, topic: str | None, now: float) -> dict:
    same = sorted(((round(cos, 2), a, b) for a in w.ids for b, cos in w.near[a]
                   if a < b and cos >= SAME and (w.inside(a, topic) or w.inside(b, topic))), reverse=True)
    pairs = set()
    for mid in w.ids:
        if w.inside(mid, topic):
            pairs.update((min(mid, o), max(mid, o)) for o in mind.conflicts(mid) if o in w.at)
    doubted = []
    for mid in w.ids:
        if w.inside(mid, topic) and (b := mind.belief(mid, now)) is not None and b.p < DOUBTFUL:
            doubted.append((b.p, {**w.shown(mid), "belief": round(b.p, 2), "why": belief.why(b, now)}))
    return {
        "say_the_same": [{"cosine": c, "a": w.shown(a), "b": w.shown(b)} for c, a, b in same],
        "in_conflict": [{"a": w.shown(a), "b": w.shown(b)} for a, b in sorted(pairs)],
        "doubtful": [d for _, d in sorted(doubted, key=lambda d: d[0])],
        "long": [{**w.shown(m), "characters": len(w.text[m])}
                 for m in sorted(w.ids, key=lambda m: -len(w.text[m]))
                 if len(w.text[m]) >= LONG and w.inside(m, topic)],
    }


def _web(w: _Whole, topic: str | None) -> dict:
    mine = [m for m in w.ids if w.inside(m, topic)]
    links = {(min(a, b), max(a, b)) for a in mine for b in w.linked[a]}
    alone = Counter(n for m in mine if not w.linked[m] for n in w.topics_of(m))
    bridges = sorted(((round(cos, 2), a, b) for a in mine for b, cos in w.near[a]
                      if RELATED <= cos < SAME and b not in w.linked[a] and w.episode.get(a) != w.episode.get(b)
                      and (a < b or not w.inside(b, topic) or a not in dict(w.near[b]))), reverse=True)
    across = []
    for m in mine:
        tops = {_top(n) for n in w.topics_of(m)}
        others = [(o, c) for o, c in w.near[m] if c >= RELATED]
        spread = tops | {_top(n) for o, _ in others for n in w.topics_of(o)}
        if len(spread - {GENERAL}) >= 3 and GENERAL not in spread and all(m < o for o, _ in others):
            across.append({**w.shown(m), "also_said_in": sorted(spread - tops), "like": [o for o, _ in others]})
    return {
        "memories": len(mine),
        "with_no_association": sum(1 for m in mine if not w.linked[m]),
        "with_no_association_by_topic": [{"topic": n, "memories": c} for n, c in alone.most_common()],
        "associations": len(links),
        "within_one_conversation": sum(1 for a, b in links if w.episode.get(a) == w.episode.get(b)),
        "alike_but_unlinked_across_conversations": [{"cosine": c, "a": w.shown(a), "b": w.shown(b)}
                                                    for c, a, b in bridges],
        "said_in_several_topics_and_not_in_general": across,
    }


def _working(mind: "Mind", topic: str | None, now: float) -> dict:
    kept = working.held(mind, [topic] if topic else [GENERAL, *(t["name"] for t in mind.topics())], now=now)
    return {"kept_long_with_no_end": [{"id": f"w{k['id']}", "in": k["topic"], "says": _cut(k["content"]),
                                       "written_days_ago": int((now - k["written_at"]) / belief.DAY)}
                                      for k in kept if k["until"] is None and now - k["written_at"] >= IDLE]}


def digest(mind: "Mind", *, section: str | None = None, topic: str | None = None,
           limit: int = 10, now: float | None = None) -> dict:
    """What stands out across the whole mind, or inside `topic`. With no section, only how much
    each section found; with one, its first `limit` of each kind and how many more there are."""
    now = time.time() if now is None else now
    if section is not None and section not in SECTIONS:
        raise ValueError(f"no section {section!r}; the sections are {', '.join(SECTIONS)}")
    w = _Whole(mind)
    found = {"topics": lambda: _topics(mind, w, topic), "memories": lambda: _memories(mind, w, topic, now),
             "web": lambda: _web(w, topic), "working": lambda: _working(mind, topic, now)}
    if section is None:
        return {"memories": len([m for m in w.ids if w.inside(m, topic)]), "topics": len(mind.topics()),
                "found": {name: {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in find().items()}
                          for name, find in found.items()}}
    out, more = {}, {}
    for kind, items in found[section]().items():
        if isinstance(items, list) and len(items) > limit:
            out[kind], more[kind] = items[:limit], len(items) - limit
        else:
            out[kind] = items
    return {"section": section, **({"topic": topic} if topic else {}), **out, **({"more": more} if more else {})}
