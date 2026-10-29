"""Bringing a mind written by an older version into today's shape.

Version 2 kept every turn of a conversation as a memory, filed in a `session.*` namespace,
and called topics namespaces. A turn is what an episode holds (docs/primitives/EPISODE.md),
so each one moves, word for word, into its session's episode, where facts can be drawn
from it. Nothing a conversation said is lost on the way, and a copy of the file is kept
beside it first.
"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapestry.mind import Mind

SESSION_PREFIX = "session."


def to_topics_and_episodes(mind: "Mind") -> None:
    from tapestry.mind import _SCHEMA
    db = mind.db
    dest = mind.path.with_name(f"{mind.path.name}.v2-{int(time.time())}.bak")
    copy = sqlite3.connect(str(dest))
    try:
        db.backup(copy)
    finally:
        copy.close()
    db.executescript(_SCHEMA)
    with db:
        db.execute("BEGIN")
        db.execute("INSERT INTO topics(id, name, description, created_at)"
                   " SELECT id, name, description, created_at FROM namespaces WHERE name NOT LIKE ?",
                   (SESSION_PREFIX + "%",))
        turns = db.execute(
            "SELECT m.id, m.key, m.content, m.created_at, e.source, e.episode,"
            " (SELECT n.name FROM memory_namespaces x JOIN namespaces n ON n.id = x.namespace_id"
            "   WHERE x.memory_id = m.id AND n.name LIKE ? ORDER BY n.name LIMIT 1),"
            " (SELECT n.name FROM memory_namespaces x JOIN namespaces n ON n.id = x.namespace_id"
            "   WHERE x.memory_id = m.id AND n.name NOT LIKE ? ORDER BY n.name LIMIT 1)"
            " FROM memories m JOIN evidence e ON e.memory_id = m.id AND e.kind = 'observe'"
            " WHERE EXISTS (SELECT 1 FROM memory_namespaces x JOIN namespaces n ON n.id = x.namespace_id"
            "   WHERE x.memory_id = m.id AND n.name LIKE ?) ORDER BY m.created_at, m.id",
            (SESSION_PREFIX + "%", SESSION_PREFIX + "%", SESSION_PREFIX + "%")).fetchall()
        for mid, key, text, ts, source, episode, session_ns, place in turns:
            eid = episode or session_ns[len(SESSION_PREFIX):]
            db.execute("INSERT OR IGNORE INTO episodes(id, place, started_at, ended_at) VALUES (?,?,?,?)",
                       (eid, place, ts, ts))
            db.execute("UPDATE episodes SET started_at=min(started_at, ?), ended_at=max(ended_at, ?),"
                       " place=coalesce(place, ?) WHERE id=?", (ts, ts, place, eid))
            db.execute("INSERT OR IGNORE INTO turns(episode, key, source, text, ts) VALUES (?,?,?,?,?)",
                       (eid, key, "user" if source == "user" else "agent", text, ts))
        moved = [t[0] for t in turns]
        for i in range(0, len(moved), 500):
            chunk = moved[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for table, col in (("evidence", "memory_id"), ("judged", "memory_id"),
                               ("memory_namespaces", "memory_id")):
                db.execute(f"DELETE FROM {table} WHERE {col} IN ({marks})", chunk)
            for table, a, b in (("judgments", "memory_id", "other_id"), ("associations", "src", "dst"),
                                ("derivations", "memory_id", "from_id")):
                db.execute(f"DELETE FROM {table} WHERE {a} IN ({marks}) OR {b} IN ({marks})", [*chunk, *chunk])
            db.execute(f"UPDATE memories SET superseded_by=NULL WHERE superseded_by IN ({marks})", chunk)
            db.execute(f"DELETE FROM memories WHERE id IN ({marks})", chunk)
        db.execute("INSERT INTO memory_topics(memory_id, topic_id) SELECT memory_id, namespace_id"
                   " FROM memory_namespaces WHERE namespace_id IN (SELECT id FROM topics)")
        db.execute("DROP TABLE memory_namespaces")
        db.execute("DROP TABLE namespaces")
        db.execute("DROP TABLE IF EXISTS derivations")
        for col in ("belief_L", "belief_t"):
            if any(r[1] == col for r in db.execute("PRAGMA table_info(memories)")):
                db.execute(f"ALTER TABLE memories DROP COLUMN {col}")
        db.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', '3')")
    mind.scrub()
    # Turns now live in episodes, so the pages they held as memories are free; give them back.
    db.execute("VACUUM")
