"""Background work: what runs when nobody is in a conversation (docs/interfaces/JOBS.md).

Hosts differ in what they offer for this, so Tapestry starts its own jobs, from the hook that
ends a session. A job runs detached, in the owner's process space, and reaches outside only
the model that makes its judgments.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

from tapestry import judge as judges
from tapestry import unrecorded
from tapestry.mind import Mind

SESSION_WINDOW = 30 * 60   # a session untouched this long is taken to have ended without saying so


class Activity:
    """Which sessions are in a conversation with a mind, so that no job runs beside one."""

    def __init__(self, directory: Path | str, window: float = SESSION_WINDOW) -> None:
        self.dir, self.window = Path(directory), window

    def _marker(self, session_id: str) -> Path:
        return self.dir / "".join(c if c.isalnum() or c in "._-" else "_" for c in session_id or "unknown")

    def touch(self, session_id: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._marker(session_id).touch()

    def end(self, session_id: str) -> None:
        self._marker(session_id).unlink(missing_ok=True)

    def busy(self) -> bool:
        """Whether any session has been in a conversation lately."""
        if not self.dir.is_dir():
            return False
        now = time.time()
        return any(now - m.stat().st_mtime < self.window for m in self.dir.iterdir())


def behind_notice(reasons: list[str]) -> str:
    """What the next session is told when background work failed partway."""
    if not reasons:
        return ""
    more = f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else ""
    return (f"[BACKGROUND WORK IS BEHIND: {reasons[0]}{more}. Judgments that were waiting haven't been"
            " made, so what follows may not reflect them.]")


def judge_pending(mind: Mind, judge: judges.Judge, *, limit: int = 25) -> int:
    """Have the judge compare each lasting memory not yet compared with the ones nearest it.

    Running it again over the same material changes nothing: a memory is marked done once
    every neighbour has an answer, and a failure partway leaves it unmarked. Returns how many
    memories were done.
    """
    done = 0
    for mid in mind.to_judge(limit):
        others = mind.neighbours(mid, prompt=judge.prompt)
        categories = judge.conflicts(mind.content(mid), others) if others else {}
        mind.record_judgments(mid, prompt=judge.prompt, model=judge.model, categories=categories)
        done += 1
    return done


def run_judging(home: Path, open_mind, behind: unrecorded.Ledger, activity: Activity,
                judge: judges.Judge | None = None) -> str:
    """One background run. Returns what happened, for the log. Never raises."""
    try:
        judge = judge or judges.configured(home)
    except judges.JudgeUnavailable as e:
        behind.add(str(e))
        return f"failed: {e}"
    if judge is None:
        return "no judge configured; nothing to do"
    if activity.busy():
        return "a conversation is in progress; waiting for the next session to end"
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "jobs.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "another job is already running"
        try:
            mind = open_mind()
            try:
                return f"judged {judge_pending(mind, judge)} memories"
            finally:
                mind.close()
        except Exception as e:
            behind.add(f"judging memories failed ({type(e).__name__}: {e})")
            return f"failed: {type(e).__name__}: {e}"


def start(log: Path) -> None:
    """Start the judging job detached. It decides for itself whether there is anything to do."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as fh:
        subprocess.Popen([sys.executable, "-m", "tapestry.cli", "judge"], stdin=subprocess.DEVNULL,
                         stdout=fh, stderr=fh, start_new_session=True, env=os.environ.copy())
