"""How much a memory is believed, read from its evidence at a moment.

docs/primitives/MEMORY.md: belief is never stored or granted. Replaying a memory's
evidence gives its confidence at any moment, so the same rules weigh evidence
whatever produced it, and changing how much a kind of source is trusted changes
future readings, never the evidence.

Two factors: whether it was right when last checked, which evidence moves by how
reliable its source is, and whether it is still true now, which decays with time at
a rate set by how quickly that kind of thing changes. The model was chosen against
two others in research/confidence_sim.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DAY = 86400.0
YEAR = 365 * DAY

# P(a report from this kind of source agrees with the truth).
RELIABILITY = {
    "user_confirmed": 0.98,   # a direct answer to a direct question outweighs everything
    "user": 0.95,
    "tool": 0.90,
    "web": 0.75,
    "agent": 0.65,            # what the agent itself said or concluded
    "inference": 0.65,
    "host_memory": 0.60,      # the host's own notebook: the agent's note, low weight
}

# Half-life of "still true now", by how quickly that kind of thing changes.
HALF_LIFE = {
    "immutable": 50 * YEAR,   # a name
    "stable": 3 * YEAR,
    "preference": 1 * YEAR,
    "project": 60 * DAY,      # a plan expires
    "transient": 7 * DAY,     # what a session was in the middle of
}
# What a memory nobody classified is assumed to be, until a model says otherwise.
UNCLASSIFIED = "preference"

_CLAMP = 6.0   # log-odds: no single record, however long, makes belief certain
# Evidence that moves belief. Filing and superseding say where a memory lives and
# whether it is current, not whether it is right.
_MOVES = {"observe", "confirm", "contradict"}


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass(frozen=True)
class Belief:
    p: float                  # how likely it is to be right now
    origin: str               # who first said it
    last_checked: float       # when evidence last bore on it
    contradicted: bool        # whether anything has contradicted it
    volatility: str | None    # how quickly it changes; None when nobody said

    @property
    def word(self) -> str:
        """Coarse on purpose: a reading, not a measurement. Never the words match uses."""
        return ("very likely" if self.p >= 0.9 else "likely" if self.p >= 0.7
                else "uncertain" if self.p >= 0.4 else "doubtful")


def read(evidence: list[dict], volatility: str | None, now: float, *,
         reliability: dict[str, float] = RELIABILITY,
         half_life: dict[str, float] = HALF_LIFE) -> Belief | None:
    """Replay evidence, oldest first, to the belief at `now`. None when nothing was ever observed.

    The first observation sets how likely the memory is to have been right, which is how
    reliable its source is. Each later confirmation or contradiction moves that by the
    source's reliability. Between entries it decays. Repeating the same thing from the
    same source in the same episode counts once, and silence is no entry at all.
    """
    h = half_life[volatility or UNCLASSIFIED]
    p, at, checked, origin, contradicted, counted = None, 0.0, 0.0, "", False, set()
    for e in sorted((e for e in evidence if e["kind"] in _MOVES), key=lambda e: (e["ts"], e.get("id", 0))):
        if p is not None:
            p *= 2 ** (-(e["ts"] - at) / h)
        at = e["ts"]
        key = (e["kind"], e["source"], e.get("episode"))
        if e.get("episode") is not None and key in counted:
            continue
        counted.add(key)
        r = reliability[e["source"]]
        if e["kind"] == "observe":
            if p is not None:       # a second observation is the memory recorded again, not a new report
                continue
            p, origin = r, e["source"]
        else:
            w = e.get("strength") or 1.0
            step = w * _logit(r)
            contradicted = contradicted or e["kind"] == "contradict"
            lo = _logit(min(max(p, _sigmoid(-_CLAMP)), _sigmoid(_CLAMP)))
            p = _sigmoid(max(-_CLAMP, min(_CLAMP, lo + (step if e["kind"] == "confirm" else -step))))
        checked = e["ts"]
    if p is None:
        return None
    return Belief(p=p * 2 ** (-(now - at) / h), origin=origin, last_checked=checked,
                  contradicted=contradicted, volatility=volatility)


_ORIGIN = {"user": "the user said it", "user_confirmed": "the user confirmed it",
           "tool": "a tool reported it", "web": "from the web", "agent": "the agent said it",
           "inference": "the agent inferred it", "host_memory": "the agent's own note"}


def ago(ts: float, now: float) -> str:
    d = max(0.0, now - ts) / DAY
    if d < 1:
        return "today"
    if d < 60:
        n = int(d)
        return f"{n} day{'s' if n != 1 else ''} ago"
    if d < 730:
        n = int(d / 30)
        return f"{n} months ago"
    return f"{int(d / 365)} years ago"


def why(b: Belief, now: float) -> str:
    """The reason belief is where it is: who said it, and how long ago anything bore on it."""
    parts = [_ORIGIN[b.origin], f"last checked {ago(b.last_checked, now)}"]
    if b.contradicted:
        parts.append("contradicted since")
    if b.volatility:
        parts.append(b.volatility)
    return ", ".join(parts)
