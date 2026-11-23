"""What a mind does at each hook, wherever it lives (docs/interfaces/HOOKS.md).

The Claude Code adapter calls these on a mind on its own machine, and the server calls the
same ones on a mind it serves, so a session behaves the same whichever way it reaches its
mind. Each takes the mind already opened and leaves opening, failing and notices that belong
to one machine to its caller.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from tapestry import guide, recall, render, working
from tapestry.mind import Mind, RecallFailed

PLACE_QUERY = "Where things stand in {place}: what was decided, what is unfinished, and who I am working with."
KEPT = 30 * 86400   # what one session left behind is of no use to another, so it is cleared out


def unavailable(why: str) -> str:
    return (f"[MEMORY UNAVAILABLE: {why}. No memories were searched. Don't treat this as "
            "evidence that memory holds nothing relevant; it wasn't consulted.]")


READ_ONLY = ("[THIS SESSION READS MEMORY ONLY: its key may read this mind but not write to it, so "
             "nothing said here will be remembered, and noting, correcting or forgetting will be refused.]")


class OpenTopics:
    """The topics each session opened beyond its place, kept by session in `folder`
    (docs/primitives/TOPIC.md: opened for one session, never another)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _file(self, session: str) -> Path:
        return self.folder / hashlib.sha256(session.encode()).hexdigest()[:24]

    def get(self, session: str | None, place: str | None) -> list[str]:
        names = [place] if place else []
        try:
            extra = json.loads(self._file(session).read_text()) if session else []
        except (OSError, ValueError):
            extra = []
        return names + [n for n in extra if n not in names]

    def save(self, session: str | None, place: str | None, names: list[str]) -> None:
        if not session:
            return
        f = self._file(session)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps([n for n in names if n != place]))
        old = time.time() - KEPT
        for other in f.parent.iterdir():
            try:
                if other.stat().st_mtime < old:
                    other.unlink()
            except OSError:
                pass


def ensure_place(mind: Mind, place: str | None, description: str | None) -> None:
    """The topic of the session's place, created the first time a session happens there."""
    if place and not any(t["name"] == place for t in mind.topics()):
        mind.topic(place, description or f"The {place} project")


def session_start(mind: Mind, *, session: str | None, place: str | None, opened: list[str],
                  attached: str, notices: list[str] = (), record: bool = True) -> str:
    """What a session starts with: notices, the guide, the topic directory, the working memory
    in view and recall's highlights for the session's place. Raises only when the mind itself
    can't be read; a recall that fails is said in the text."""
    topics = mind.topics()
    try:
        kept = render.working(working.in_view(mind, opened), time.time())
    except Exception as e:
        kept = "\n\n" + unavailable(f"working memory couldn't be read ({type(e).__name__}: {e})")
    try:
        auto = recall.session_start(
            mind, PLACE_QUERY.format(place=f"the {place} project" if place else "my work"), opened)
        if record and session:
            mind.episode(session, place).add_recalled(h.id for h in auto.highlights)
        shown = render.automatic(auto)
        recalled = ("\n\nWhat you remember about where things stand:\n" + shown) if shown else ""
    except RecallFailed as e:
        recalled = "\n\n" + unavailable(f"recall for the session's place failed ({e})")
    gap = "\n".join(n for n in (*notices, *([] if record else [READ_ONLY])) if n)
    return ((gap + "\n\n") if gap else "") \
        + f"Long-term memory (tapestry) is on, attached to the mind {attached}. Searching the general " \
          f"topic{' plus ' + ', '.join(opened) if opened else ''}.\n\n" \
        + guide.GUIDE + render.directory(topics, opened) + kept + recalled


def before_turn(mind: Mind, *, session: str | None, place: str | None, prompt: str, opened: list[str],
                notices: list[str] = (), record: bool = True) -> str | None:
    """Recall's highlights and hints for this prompt, after any notices; None when there is
    nothing to show. A recall that fails is said in the text."""
    gap = "\n".join(n for n in notices if n)
    gap = gap + "\n" if gap else ""
    try:
        auto = recall.before_turn(mind, prompt, opened)
        if record and session:
            mind.episode(session, place).add_recalled(h.id for h in auto.highlights)
    except RecallFailed as e:
        return gap + unavailable(f"recall failed ({e})")
    shown = render.automatic(auto)
    if not shown:
        return gap.strip() or None
    return gap + "From long-term memory:\n" + shown


def add_turns(mind: Mind, *, session: str, place: str | None, turns: list[dict]) -> dict:
    """Add what the user and the agent said to the session's episode. A turn added before,
    under the same key, is added once (docs/primitives/EPISODE.md)."""
    ep = mind.episode(session, place)
    stats = {"added": 0, "already": 0}
    for t in turns:
        stats["added" if ep.add_turn(t["key"], t["source"], t["text"], t.get("ts")) else "already"] += 1
    return stats


def end(mind: Mind, *, session: str, place: str | None) -> None:
    mind.episode(session, place).end()
