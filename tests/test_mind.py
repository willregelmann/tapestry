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
    hits = mind.recall("What is Sam's favorite color?", scopes=["user"])
    assert [h.id for h in hits] == [new]
    assert mind.evidence(old)[-1]["kind"] == "supersede"


def test_recall_sees_only_loaded_scopes(mind):
    mind.scope("atlas")
    mind.scope("birch")
    a = mind.remember("This project runs its tests with pytest.", source="user", scope="atlas")
    mind.remember("This project runs its tests with vitest.", source="user", scope="birch")
    hits = mind.recall("How do I run the tests?", scopes=["user", "atlas"])
    assert [h.id for h in hits] == [a]
    assert all(h.scope in ("user", "atlas") for h in hits)


def test_unknown_scope_loads_nothing(mind):
    mind.remember("Sam likes tea.", source="user")
    assert mind.recall("tea", scopes=["nope"]) == []


def test_keyword_only_hits_are_labelled_as_such(mind):
    mind.remember("zyzzyva alpha beta gamma delta epsilon", source="user")
    hits = mind.recall("zyzzyva", scopes=["user"])
    assert hits and hits[0].via in ("keyword", "both")


def test_recall_fails_loudly_when_it_cannot_search(tmp_path):
    ok = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    ok.remember("Sam likes tea.", source="user")
    ok.close()

    def broken(texts):
        raise OSError("model file vanished")

    m = Mind(tmp_path / "m.db", encode=broken, model_tag="toy")
    with pytest.raises(RecallFailed, match="model file vanished"):
        m.recall("tea", scopes=["user"])
    m.close()


def test_recall_refuses_to_mix_encoders(tmp_path):
    a = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    a.remember("Sam likes tea.", source="user")
    a.close()
    b = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="other-model")
    with pytest.raises(RecallFailed, match="lack a other-model embedding"):
        b.recall("tea", scopes=["user"])
    b.close()
