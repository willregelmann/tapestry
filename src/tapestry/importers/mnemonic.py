"""One-way import from mnemonic's store (Hermes holographic schema) into a mind.

    python -m tapestry.importers.mnemonic ~/.hermes/memory_store.db ~/.hermes/tapestry/mind.db
    python -m tapestry.importers.mnemonic SRC DST --dry-run

The source is opened read-only and never modified. Every fact becomes a memory
in the user-wide scope, learned at its original created_at time, with
first evidence from source "agent": mnemonic's facts were written by the agent
through its tools, so they carry the agent's authority, not the user's.
Importing twice changes nothing, because each memory's key is derived from
its mnemonic fact id. Facts below mnemonic's own trust floor (0.3) are skipped
and counted, never dropped silently.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

from tapestry.mind import USER_SCOPE, Mind

TRUST_FLOOR = 0.3  # mnemonic never served facts below this


def _ts(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"unrecognized created_at {value!r}")


def read_facts(src: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT fact_id, content, category, tags, trust_score, created_at"
                            " FROM facts ORDER BY fact_id").fetchall()
    finally:
        conn.close()


def import_facts(src: Path, mind: Mind) -> dict:
    facts = read_facts(src)
    stats = {"facts": len(facts), "imported": 0, "already_present": 0, "below_trust_floor": 0}
    for f in facts:
        if (f["trust_score"] or 0) < TRUST_FLOOR:
            stats["below_trust_floor"] += 1
            continue
        key = f"mnemonic:{f['fact_id']}"
        exists = mind.db.execute("SELECT 1 FROM memories WHERE key=?", (key,)).fetchone()
        if exists:
            stats["already_present"] += 1
            continue
        mind.remember(f["content"], source="agent", scope=USER_SCOPE,
                      at=_ts(f["created_at"]), key=key, episode="import:mnemonic")
        stats["imported"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="mnemonic's memory_store.db")
    ap.add_argument("dst", type=Path, help="the tapestry mind to import into")
    ap.add_argument("--dry-run", action="store_true", help="count, don't write")
    args = ap.parse_args(argv)
    if not args.src.is_file():
        print(f"{args.src} not found", file=sys.stderr)
        return 2
    if args.dry_run:
        facts = read_facts(args.src)
        low = sum((f["trust_score"] or 0) < TRUST_FLOOR for f in facts)
        print(f"{len(facts)} facts; {len(facts) - low} would import, {low} below the trust floor")
        return 0
    from tapestry import embed
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    mind = Mind(args.dst, encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
    try:
        stats = import_facts(args.src, mind)
    finally:
        mind.close()
    print(", ".join(f"{k}: {v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
