"""Moments a host couldn't record, kept until the agent is told.

docs/primitives/MEMORY.md: a moment that can't be recorded is reported, never
silently lost. Recording happens where nobody is watching (a detached process in
Claude Code, a writer thread in Hermes), so a failure there is written to a
ledger beside the mind and shown in the agent's context at its next chance.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class Ledger:
    """An append-only file of failures. Safe across processes: appends are single
    writes, and `take` claims the whole file by renaming it."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def add(self, why: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({"ts": time.time(), "why": why}) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, entry.encode())
        finally:
            os.close(fd)

    def take(self) -> list[str]:
        """Every reason recorded so far, cleared so each failure is told once."""
        claimed = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}")
        try:
            os.rename(self.path, claimed)
        except FileNotFoundError:
            return []
        try:
            lines = claimed.read_text().splitlines()
        finally:
            claimed.unlink(missing_ok=True)
        out = []
        for line in lines:
            try:
                out.append(json.loads(line)["why"])
            except (ValueError, KeyError):
                out.append("an unreadable failure record")
        return out


def notice(reasons: list[str]) -> str:
    """What the agent is shown. Empty when nothing was lost."""
    if not reasons:
        return ""
    first = reasons[0]
    more = f" (and {len(reasons) - 1} more failures)" if len(reasons) > 1 else ""
    return (f"[MEMORY GAP: {len(reasons)} recording {'failure' if len(reasons) == 1 else 'failures'}"
            f" since you last looked: {first}{more}. What was being recorded is not in memory, so"
            " don't take its absence from recall to mean it never happened.]")
