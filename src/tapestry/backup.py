"""Nightly backups of every mind a server holds (PLAN.md stage 6).

Each mind is copied with SQLite's own backup, which is safe while the server writes, into a
dated file. Older copies than `--keep` are deleted, so forgetting reaches the backups within
that many days (docs/primitives/MEMORY.md: every copy kept for safekeeping catches up).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

from tapestry import server


def back_up(home: Path, to: Path, keep: int, now: float | None = None) -> list[Path]:
    now = time.time() if now is None else now
    stamp = dt.datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    to.mkdir(parents=True, exist_ok=True)
    made = []
    for name, path in server.Registry(home).minds().items():
        if not Path(path).is_file():
            continue
        dest = to / f"{name}-{stamp}.db"
        src, out = sqlite3.connect(f"file:{path}?mode=ro", uri=True), sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            src.close()
            out.close()
        dest.chmod(0o600)
        made.append(dest)
    for old in to.glob("*.db"):
        if old.stat().st_mtime < now - keep * 86400:
            old.unlink()
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tapestry.backup")
    ap.add_argument("--to", required=True)
    ap.add_argument("--keep", type=int, default=14, help="days a copy is kept")
    ap.add_argument("--every", type=float, default=0, help="seconds between backups; 0 backs up once")
    ap.add_argument("--home", default=os.environ.get("TAPESTRY_SERVER_HOME") or str(Path.home() / ".tapestry-server"))
    a = ap.parse_args(argv)
    while True:
        made = back_up(Path(a.home), Path(a.to), a.keep)
        print(f"{dt.datetime.now().isoformat(timespec='seconds')} backed up {len(made)} minds", flush=True)
        if not a.every:
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
