"""Consolidate: the automatic path into memory (docs/capabilities/CONSOLIDATE.md).

A model reads what an episode said and proposes the facts it taught. Deciding what was
taught, and what each fact builds on, is the model's judgment. Everything else is fixed
here, whatever the model says:
- a fact cites the turns it came from, and is attributed to whoever said them;
- it can only build on, repeat or replace memories the model was shown;
- a fact the mind already holds gets the conversation as evidence, never a second copy;
- facts drawn from one moment are linked to each other and to what they build on;
- it only adds; a replacement goes through the same supersession as any other;
- running it twice over the same turns changes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Protocol

from tapestry import belief
from tapestry.episode import Episode, waiting
from tapestry.mind import GENERAL, Mind, topic_name

WINDOW_CHARS = 48_000   # what one call reads at most
TURN_CHARS = 8_000      # what one turn shows at most, so one long paste can't fill a window
KNOWN = 40              # the most memories a model is shown beside a window

SAID_BY = {"user": "user", "agent": "agent", "inferred": "inference"}


class Drawer(Protocol):
    prompt: str    # what it was asked
    model: str     # who answers

    def draw(self, *, place: str | None, date: str, topics: list[dict], known: list[tuple[int, str]],
             turns: list[tuple[int, str, str]]) -> dict:
        """The facts these turns taught, said on `date`: {"facts": [...], "new_topics": [...]}. Each fact has
        content, said_by (user, agent or inferred), turns (the numbers given), topics, and
        optionally volatility, builds_on, same_as and replaces (ids from `known`)."""


def windows(turns: list[dict]) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for t in turns:
        n = min(len(t["text"]), TURN_CHARS)
        if cur and size + n > WINDOW_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += n
    return out + ([cur] if cur else [])


def _shown(text: str) -> str:
    return text if len(text) <= TURN_CHARS else text[:TURN_CHARS] + f" [cut: {len(text)} chars]"


def _known(mind: Mind, ep: Episode, window: list[dict]) -> list[tuple[int, str]]:
    """What the model may say a fact builds on, repeats or replaces: what was in mind during
    these turns, and what the mind holds nearest what the user said."""
    ids = ep.recalled_between(window[0]["ts"], window[-1]["ts"])
    every = [t["name"] for t in mind.topics()]
    for t in window:
        if t["source"] == "user":
            ids += [r.id for r in mind.recall(t["text"][:1000], topics=every, k=3)]
    ids = list(dict.fromkeys(ids))[:KNOWN]
    return [(i, mind.content(i)) for i in ids]


def draw_episode(mind: Mind, ep: Episode, drawer: Drawer) -> dict:
    """Draw facts from every turn of `ep` not drawn yet. Returns what was done."""
    stats = {"facts": 0, "confirmed": 0, "replaced": 0, "dropped": 0, "windows": 0}
    for window in windows(ep.turns(after=ep.drawn_through)):
        known = _known(mind, ep, window)
        known_ids = {i for i, _ in known}
        date = dt.datetime.fromtimestamp(window[0]["ts"], dt.timezone.utc).strftime("%A %Y-%m-%d")
        answer = drawer.draw(place=ep.place, date=date, topics=mind.topics(), known=known,
                             turns=[(i, t["source"], _shown(t["text"])) for i, t in enumerate(window, 1)])
        described = {}
        for nt in answer.get("new_topics") or []:
            try:
                described[topic_name(nt["name"])] = str(nt.get("description") or "").strip()
            except (KeyError, TypeError, ValueError):
                continue
        existing = {t["name"] for t in mind.topics()}
        born: list[int] = []
        builds: set[int] = set()
        for fact in answer.get("facts") or []:
            done = _record(mind, ep, drawer, window, fact, known_ids, existing, described, born)
            if done is None:
                stats["dropped"] += 1
                continue
            kind, mid, links = done
            stats[kind] += 1
            builds |= links
        if born:
            mind.strengthen(set(ep.recalled_between(window[0]["ts"], window[-1]["ts"])) | builds,
                            at=window[-1]["ts"])
        ep.mark_drawn(window[-1]["seq"])
        stats["windows"] += 1
    return stats


def _record(mind, ep, drawer, window, fact, known_ids, existing, described, born):
    """One proposed fact, through the checks. None when it can't stand."""
    content = str(fact.get("content") or "").strip()
    cited = [window[i - 1] for i in fact.get("turns") or [] if isinstance(i, int) and 1 <= i <= len(window)]
    if not content or not cited:
        return None
    said = SAID_BY.get(fact.get("said_by"), "inference")
    # Attributed to whoever said it: a claim put in a speaker's mouth that none of its turns
    # has them saying is the model's inference.
    if said in ("user", "agent") and not any(t["source"] == said for t in cited):
        said = "inference"
    ids = lambda f: {i for i in (fact.get(f) if isinstance(fact.get(f), list) else [fact.get(f)])
                     if isinstance(i, int) and i in known_ids and mind.superseded_by(i) is None}
    same, replaces, builds_on = ids("same_as"), ids("replaces"), ids("builds_on")
    keys = [t["key"] for t in cited]
    at = cited[-1]["ts"]
    if same:
        mid = min(same)
        mind.confirm(mid, source=said, at=at, episode=ep.id, reasoner=drawer.model, drawn_from=keys)
        return "confirmed", mid, {mid} | builds_on
    topics = set()
    for name in fact.get("topics") or []:
        if name == GENERAL:
            continue
        if name in existing or name in described:
            topics.add(name)
    if not topics and GENERAL not in (fact.get("topics") or []) and ep.place:
        topics.add(ep.place)    # unclear where it matters: narrowly, where it was said
    for name in topics - existing:
        mind.topic(name, described.get(name) or f"Created while drawing facts from {ep.id}")
        existing.add(name)
    volatility = fact.get("volatility") if fact.get("volatility") in belief.HALF_LIFE else None
    old = min(replaces) if replaces else None
    key = "drawn:" + hashlib.sha256(f"{ep.id}\0{window[-1]['seq']}\0{content}".encode()).hexdigest()
    mid = mind.remember(content, source=said, topics=sorted(topics), at=at, episode=ep.id, key=key,
                        volatility=volatility, supersedes=old, superseded_by_judgment_of="inference",
                        associate=[*born, *sorted(builds_on), *([old] if old else [])],
                        drawn_from=keys, reasoner=drawer.model)
    born.append(mid)
    return ("replaced" if old else "facts"), mid, builds_on


def consolidate(mind: Mind, drawer: Drawer, *, limit: int = 10) -> dict:
    """Draw facts from up to `limit` episodes that have ended with turns still to draw."""
    total = {"episodes": 0}
    for ep in waiting(mind)[:limit]:
        for k, v in draw_episode(mind, ep, drawer).items():
            total[k] = total.get(k, 0) + v
        total["episodes"] += 1
    return total
