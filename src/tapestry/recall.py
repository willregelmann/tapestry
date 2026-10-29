"""Recall's automatic path: highlights and hints, never everything found (docs/capabilities/RECALL.md).

Highlights are the few memories that clearly bear on the moment, shown in full. Hints say where
more may be: further matches in the topics searched, and unopened topics whose name and
description suggest they bear on it. Context is the agent's to spend, so it follows a hint with
tapestry_recall when it decides to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tapestry.mind import Mind, Recalled

HIGHLIGHTS = 3
# How many results recall looks through for further matches to hint at, and the least cosine
# for one to be hinted at. Measured on the crowded LongMemEval mind: the answer's facts are
# among the top ten at or above 0.35 for 29 of 30 questions, beside about five others.
DEPTH = 10
HINT_FLOOR = 0.35
# Unopened topics hinted at, best first, and the least cosine between the moment and a topic's
# name and description to hint at it. Measured on the crowded LongMemEval mind: the best two
# topics by description hold the answer's facts for 29 of 30 questions.
TOPIC_HINTS = 3
TOPIC_FLOOR = 0.15


@dataclass(frozen=True)
class Automatic:
    highlights: list[Recalled]
    more: dict[str, int] = field(default_factory=dict)   # searched topic ("" for general) -> further matches
    topics: list[str] = field(default_factory=list)      # unopened topics that may bear on the moment


def before_turn(mind: Mind, moment: str, topics: list[str]) -> Automatic:
    """Highlights are clear matches only; a moment nothing clearly matches gets none."""
    hits = mind.recall(moment, topics=topics, k=DEPTH)
    clear = [h for h in hits if h.brought_by is None and h.match == "ok"]
    return _automatic(mind, moment, topics, hits, clear[:HIGHLIGHTS])


def session_start(mind: Mind, place_query: str, topics: list[str]) -> Automatic:
    """A session start has no moment to match, only its place, so its highlights are the few
    memories that best show where things stand there, each still labelled with how well it fits."""
    hits = mind.recall(place_query, topics=topics, k=DEPTH)
    return _automatic(mind, place_query, topics, hits, [h for h in hits if h.brought_by is None][:HIGHLIGHTS])


def _automatic(mind: Mind, moment: str, topics: list[str], hits: list[Recalled],
               highlights: list[Recalled]) -> Automatic:
    shown = {h.id for h in highlights}
    more: dict[str, int] = {}
    for h in hits:
        if h.id not in shown and (h.cosine >= HINT_FLOOR or h.brought_by in shown):
            for t in h.topics or ("",):
                more[t] = more.get(t, 0) + 1
    return Automatic(highlights, more, _bearing(mind, moment, topics))


def _bearing(mind: Mind, moment: str, opened: list[str]) -> list[str]:
    """Unopened topics whose name and description bear on the moment. Their contents are never
    read, so an unsearched topic still leaves no trace (docs/primitives/TOPIC.md)."""
    closed = [t for t in mind.topics() if t["name"] not in opened]
    if not closed:
        return []
    vecs = mind._encode([moment] + [f"{t['name']}: {t['description'] or t['name']}" for t in closed])
    scores = vecs[1:] @ vecs[0]
    ranked = sorted(zip(scores, (t["name"] for t in closed)), reverse=True)
    return [name for s, name in ranked[:TOPIC_HINTS] if s >= TOPIC_FLOOR]
