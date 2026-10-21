"""The Stage 1 invariants for Remember and Recall, checked with a toy encoder."""

from __future__ import annotations

import hashlib
import re
import sqlite3

import numpy as np
import pytest

from tapestry.mind import Mind, RecallFailed

DIM = 64


def toy_encode(texts: list[str]) -> np.ndarray:
    """Bag of hashed words: same words, same direction. Enough to test plumbing."""
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z]+", t.lower()):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
        n = np.linalg.norm(out[i])
        out[i] = out[i] / n if n else out[i]
    return out


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    yield m
    m.close()


def test_remember_records_first_evidence(mind):
    mid = mind.remember("Sam's favorite color is pink.", source="user", at=100.0)
    ev = mind.evidence(mid)
    assert ev == [{"kind": "observe", "source": "user", "episode": None, "note": None, "ts": 100.0}]


def test_remember_is_idempotent(mind):
    a = mind.remember("Sam likes tea.", source="user", at=1.0)
    b = mind.remember("Sam likes tea.", source="user", at=1.0)
    assert a == b
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
    assert len(mind.evidence(a)) == 1


def test_remember_rejects_unknown_source_and_empty_content(mind):
    with pytest.raises(ValueError):
        mind.remember("x", source="rumor")
    with pytest.raises(ValueError):
        mind.remember("   ", source="user")


def test_content_and_evidence_cannot_be_edited(mind):
    mid = mind.remember("Sam likes tea.", source="user")
    with pytest.raises(sqlite3.IntegrityError, match="never edited"):
        mind.db.execute("UPDATE memories SET content='Sam likes coffee.' WHERE id=?", (mid,))
    with pytest.raises(sqlite3.IntegrityError, match="never edited"):
        mind.db.execute("UPDATE evidence SET source='web'")


def test_superseded_memory_is_never_recalled_as_current(mind):
    old = mind.remember("Sam's favorite color is pink.", source="user", at=1.0)
    new = mind.remember("Sam's favorite color is teal.", source="user", at=2.0, supersedes=old)
    hits = mind.recall("What is Sam's favorite color?", namespaces=[])
    assert [h.id for h in hits] == [new]
    assert mind.evidence(old)[-1]["kind"] == "supersede"


def test_a_superseded_memory_cannot_be_superseded_again(mind):
    # Recording a second replacement would claim a history that didn't happen.
    old = mind.remember("Sam's favorite color is pink.", source="user", at=1.0)
    new = mind.remember("Sam's favorite color is teal.", source="user", at=2.0, supersedes=old)
    with pytest.raises(ValueError, match="already superseded"):
        mind.remember("Sam's favorite color is red.", source="user", at=3.0, supersedes=old)
    assert mind.superseded_by(old) == new
    assert [e["note"] for e in mind.evidence(old) if e["kind"] == "supersede"] == [f"superseded by {new}"]


def _bytes_of(mind):
    """Everything the mind has on disk: the database and its write-ahead log."""
    out = b""
    for suffix in ("", "-wal"):
        f = mind.path.with_name(mind.path.name + suffix)
        out += f.read_bytes() if f.exists() else b""
    return out


def test_forgetting_leaves_nothing_that_says_what_the_memory_held(mind):
    # docs/primitives/MEMORY.md: forgetting is complete, and afterward nothing says what it held.
    keep = mind.remember("Sam keeps bees in Vermont.", source="user", namespaces=["bees"])
    gone = mind.remember("Sam's locker code is zyxwvut4417.", source="user", namespaces=["school"])
    mind.db.execute("INSERT INTO associations(src, dst, strength) VALUES (?, ?, 0.5)", (keep, gone))
    mind.db.execute("INSERT INTO associations(src, dst, strength) VALUES (?, ?, 0.5)", (gone, keep))
    assert b"zyxwvut4417" in _bytes_of(mind)          # the check can see it while it is there
    mind.forget(gone)
    assert b"zyxwvut4417" not in _bytes_of(mind)
    assert mind.recall("locker code zyxwvut4417", namespaces=["school", "bees"]) == []
    for table, col in [("memories", "id"), ("evidence", "memory_id"), ("memory_namespaces", "memory_id"),
                       ("associations", "src"), ("associations", "dst")]:
        assert mind.db.execute(f"SELECT count(*) FROM {table} WHERE {col}=?", (gone,)).fetchone()[0] == 0
    assert mind.recall("Sam bees Vermont", namespaces=["bees"])[0].id == keep   # the rest is untouched
    assert len(mind.evidence(keep)) == 2                                         # observe + file


def test_the_mind_remembers_that_something_was_forgotten_never_what(mind):
    gone = mind.remember("Sam's locker code is 4417.", source="user", key="cc:u1")
    mind.forget(gone)
    assert mind.was_forgotten(gone) and mind.recorded("cc:u1")
    assert mind.remember("Sam's locker code is 4417.", source="user", key="cc:u1") == gone
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
    again = mind.remember("Sam's gym code is 9981.", source="user")
    assert again != gone and not mind.was_forgotten(again)     # a new memory never takes its id


def test_an_inference_goes_when_the_last_memory_it_came_from_is_forgotten(mind):
    a = mind.remember("Sam said he runs on Tuesdays.", source="user")
    b = mind.remember("Sam said he runs on Thursdays.", source="user")
    both = mind.remember("Sam runs twice a week.", source="inference")
    solo = mind.remember("Sam runs on Tuesdays.", source="inference")
    mind.db.executemany("INSERT INTO derivations VALUES (?, ?)", [(both, a), (both, b), (solo, a)])
    assert mind.forgetting(a).ids == sorted([a, solo])        # a preview takes nothing
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 4
    mind.forget(a)
    left = {r[0] for r in mind.db.execute("SELECT id FROM memories")}
    assert left == {b, both}
    mind.forget(b)
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_forgetting_a_correction_makes_what_it_corrected_current_again(mind):
    old = mind.remember("Sam's favorite color is pink.", source="user", at=1.0)
    new = mind.remember("Sam's favorite color is teal.", source="user", at=2.0, supersedes=old)
    done = mind.forget(new)
    assert done.restored == [old]
    assert mind.superseded_by(old) is None
    assert [h.id for h in mind.recall("favorite color", namespaces=[])] == [old]
    assert not [e for e in mind.evidence(old) if e["kind"] == "supersede"]


def test_superseding_an_unknown_memory_rolls_back_the_whole_note(mind):
    before = mind.db.execute("SELECT count(*) FROM memories").fetchone()[0]
    with pytest.raises(KeyError):
        mind.remember("Sam's favorite color is teal.", source="user", supersedes=99999)
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == before
    assert mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_recall_sees_default_plus_searched_namespaces(mind):
    a = mind.remember("This project runs its tests with pytest.", source="user", namespaces=["atlas"])
    mind.remember("This project runs its tests with vitest.", source="user", namespaces=["birch"])
    d = mind.remember("Sam prefers pytest-style test names.", source="user")
    hits = mind.recall("How do I run the tests?", namespaces=["atlas"])
    assert {h.id for h in hits} == {a, d}
    assert {h.namespaces for h in hits} == {("atlas",), ()}
    assert all(h.namespaces == () for h in mind.recall("vitest"))


def test_a_memory_in_several_namespaces_is_seen_through_any(mind):
    mind.namespace("atlas")
    s = mind.remember("Sam's salary is $92,000.", source="user", namespaces=["sam", "finance"])
    assert [h.id for h in mind.recall("salary", namespaces=["finance"])] == [s]
    assert [h.id for h in mind.recall("salary", namespaces=["sam"])] == [s]
    assert mind.recall("salary", namespaces=["atlas"]) == []
    assert mind.recall("salary") == []


def test_unsearched_namespaces_leave_no_trace_in_keyword_ranking(mind):
    # Enough hidden keyword matches to fill any LIMIT must not crowd out a visible one.
    for i in range(40):
        mind.remember(f"zebra hidden note {i}", source="user", namespaces=["secret"])
    v = mind.remember("zebra visible note", source="user")
    assert [h.id for h in mind.recall("zebra", k=1)] == [v]


def test_filing_and_unfiling_are_evidence(mind):
    m = mind.remember("Groceries run about $600 a month.", source="user")
    mind.file(m, "finance", source="agent")
    mind.file(m, "finance", source="agent")  # idempotent: no second evidence
    mind.unfile(m, "finance", source="user")
    kinds = [(e["kind"], e["note"]) for e in mind.evidence(m)]
    assert kinds == [("observe", None), ("file", "finance"), ("unfile", "finance")]


def test_namespaces_list_descriptions_not_contents(mind):
    mind.namespace("finance", "Money: budgets, salary, taxes.")
    mind.remember("Groceries run about $600 a month.", source="user", namespaces=["finance"])
    assert mind.namespaces() == [{"name": "finance", "description": "Money: budgets, salary, taxes.",
                                  "memories": 1}]
    with pytest.raises(ValueError):
        mind.namespace("Bad Name")


def test_unknown_namespace_searches_only_the_default(mind):
    d = mind.remember("Sam likes tea.", source="user")
    assert [h.id for h in mind.recall("tea", namespaces=["nope"])] == [d]


def test_keyword_only_hits_are_labelled_as_such(mind):
    mind.remember("zyzzyva alpha beta gamma delta epsilon", source="user")
    hits = mind.recall("zyzzyva", namespaces=[])
    assert hits and hits[0].via in ("keyword", "both")


def test_recall_fails_loudly_when_it_cannot_search(tmp_path):
    ok = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    ok.remember("Sam likes tea.", source="user")
    ok.close()

    def broken(texts):
        raise OSError("model file vanished")

    m = Mind(tmp_path / "m.db", encode=broken, model_tag="toy")
    with pytest.raises(RecallFailed, match="model file vanished"):
        m.recall("tea", namespaces=[])
    m.close()


def test_recall_refuses_to_mix_encoders(tmp_path):
    a = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    a.remember("Sam likes tea.", source="user")
    a.close()
    b = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="other-model")
    with pytest.raises(RecallFailed, match="lack a other-model embedding"):
        b.recall("tea", namespaces=[])
    b.close()


def test_recall_never_adds_evidence(mind):
    # Only what happens outside the mind moves belief (docs/primitives/MEMORY.md):
    # recalling a memory, however often, is never evidence for it.
    mid = mind.remember("Sam likes tea.", source="user")
    before = mind.evidence(mid)
    for _ in range(3):
        assert mind.recall("what does Sam like")
    assert mind.evidence(mid) == before


def test_every_evidence_entry_names_a_known_source(mind):
    # Every entry names its source (docs/primitives/EVIDENCE.md), from the known kinds.
    mid = mind.remember("Sam likes tea.", source="user")
    with pytest.raises(ValueError, match="unknown source"):
        mind.file(mid, "finance", source="rumor")
    newer = mind.remember("Sam likes coffee now.", source="user")
    with pytest.raises(ValueError, match="unknown source"):
        mind.supersede(mid, newer, source="rumor")
    assert [e["source"] for e in mind.evidence(mid)] == ["user"]
