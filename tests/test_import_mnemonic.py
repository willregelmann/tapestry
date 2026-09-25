"""Importing mnemonic's store: read-only, idempotent, honest about what it skips."""

from __future__ import annotations

import hashlib
import sqlite3

from tapestry.importers import mnemonic
from tapestry.mind import Mind
from tests.test_mind import toy_encode


def make_store(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE facts (fact_id INTEGER PRIMARY KEY, content TEXT UNIQUE,"
                 " category TEXT, tags TEXT, trust_score REAL, created_at TIMESTAMP)")
    conn.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?)", [
        (1, "Sam's router is in the hallway closet.", "infra", "", 0.5, "2026-08-01 10:00:00"),
        (2, "Sam takes coffee black.", "general", "", 0.9, "2026-08-02 11:30:00"),
        (3, "An unreliable rumor.", "general", "", 0.1, "2026-08-03 12:00:00"),
    ])
    conn.commit()
    conn.close()


def test_import_is_read_only_idempotent_and_counts_skips(tmp_path):
    src = tmp_path / "memory_store.db"
    make_store(src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    mind = Mind(tmp_path / "mind.db", encode=toy_encode, model_tag="toy")
    first = mnemonic.import_facts(src, mind)
    assert first == {"facts": 3, "imported": 2, "already_present": 0, "below_trust_floor": 1}
    second = mnemonic.import_facts(src, mind)
    assert second["imported"] == 0 and second["already_present"] == 2
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    rows = mind.db.execute("SELECT m.content, m.created_at, e.source, e.episode FROM memories m"
                           " JOIN evidence e ON e.memory_id = m.id ORDER BY m.id").fetchall()
    assert rows[0][0] == "Sam's router is in the hallway closet."
    assert rows[0][2:] == ("agent", "import:mnemonic")
    assert rows[1][1] > rows[0][1]  # original learned-at times are kept
    mind.close()
