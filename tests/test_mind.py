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
