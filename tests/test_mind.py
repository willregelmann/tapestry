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
    hits = mind.recall("What is Sam's favorite color?", topics=[])
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
    keep = mind.remember("Sam keeps bees in Vermont.", source="user", topics=["bees"])
    gone = mind.remember("Sam's locker code is zyxwvut4417.", source="user", topics=["school"],
                         associate=[keep])
    assert b"zyxwvut4417" in _bytes_of(mind)          # the check can see it while it is there
    mind.forget(gone)
    assert b"zyxwvut4417" not in _bytes_of(mind)
    assert mind.recall("locker code zyxwvut4417", topics=["school", "bees"]) == []
    for table, col in [("memories", "id"), ("evidence", "memory_id"), ("memory_topics", "memory_id"),
                       ("associations", "src"), ("associations", "dst")]:
        assert mind.db.execute(f"SELECT count(*) FROM {table} WHERE {col}=?", (gone,)).fetchone()[0] == 0
    assert mind.recall("Sam bees Vermont", topics=["bees"])[0].id == keep   # the rest is untouched
    assert len(mind.evidence(keep)) == 2                                     # observe + file


def test_forgetting_a_memory_removes_the_words_it_came_from_out_of_every_episode(mind):
    # docs/primitives/EPISODE.md: afterward no episode says what was forgotten.
    ep = mind.episode("s1")
    ep.add_turn("t1", "user", "My locker code is qwrtzp4417, by the way.", 1.0)
    ep.add_turn("t2", "agent", "Got it.", 2.0)
    mid = mind.remember("Sam's locker code is qwrtzp4417.", source="user", episode="s1", drawn_from=["t1"])
    mind.forget(mid)
    assert b"qwrtzp4417" not in _bytes_of(mind)
    assert [t["key"] for t in ep.turns()] == ["t2"]
    assert not ep.add_turn("t1", "user", "My locker code is qwrtzp4417, by the way.", 1.0)  # read again: not back


def test_the_mind_remembers_that_something_was_forgotten_never_what(mind):
    gone = mind.remember("Sam's locker code is 4417.", source="user", key="cc:u1")
    mind.forget(gone)
    assert mind.was_forgotten(gone) and mind.recorded("cc:u1")
    assert mind.remember("Sam's locker code is 4417.", source="user", key="cc:u1") == gone
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
    again = mind.remember("Sam's gym code is 9981.", source="user")
    assert again != gone and not mind.was_forgotten(again)     # a new memory never takes its id


def test_forgetting_a_correction_makes_what_it_corrected_current_again(mind):
    old = mind.remember("Sam's favorite color is pink.", source="user", at=1.0)
    new = mind.remember("Sam's favorite color is teal.", source="user", at=2.0, supersedes=old)
    done = mind.forget(new)
    assert done.restored == [old]
    assert mind.superseded_by(old) is None
    assert [h.id for h in mind.recall("favorite color", topics=[])] == [old]
    assert not [e for e in mind.evidence(old) if e["kind"] == "supersede"]


def test_superseding_an_unknown_memory_rolls_back_the_whole_note(mind):
    before = mind.db.execute("SELECT count(*) FROM memories").fetchone()[0]
    with pytest.raises(KeyError):
        mind.remember("Sam's favorite color is teal.", source="user", supersedes=99999)
    assert mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == before
    assert mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_recall_sees_the_general_topic_plus_searched_topics(mind):
    a = mind.remember("This project runs its tests with pytest.", source="user", topics=["atlas"])
    mind.remember("This project runs its tests with vitest.", source="user", topics=["birch"])
    d = mind.remember("Sam prefers pytest-style test names.", source="user")
    hits = mind.recall("How do I run the tests?", topics=["atlas"])
    assert {h.id for h in hits} == {a, d}
    assert {h.topics for h in hits} == {("atlas",), ()}
    assert all(h.topics == () for h in mind.recall("vitest"))


def test_a_memory_in_several_topics_is_seen_through_any(mind):
    mind.topic("atlas")
    s = mind.remember("Sam's salary is $92,000.", source="user", topics=["sam", "finance"])
    assert [h.id for h in mind.recall("salary", topics=["finance"])] == [s]
    assert [h.id for h in mind.recall("salary", topics=["sam"])] == [s]
    assert mind.recall("salary", topics=["atlas"]) == []
    assert mind.recall("salary") == []


def test_unsearched_topics_leave_no_trace_in_keyword_ranking(mind):
    # Enough hidden keyword matches to fill any LIMIT must not crowd out a visible one. The
    # visible one shares too few words to pass the semantic floor, so only keyword finds it.
    for i in range(40):
        mind.remember(f"zebra hidden note {i}", source="user", topics=["secret"])
    filler = " ".join(f"w{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(60))
    v = mind.remember(f"zebra {filler}", source="user")
    hits = mind.recall("zebra", k=1)
    assert [h.id for h in hits] == [v] and hits[0].via == "keyword"


def test_filing_and_unfiling_are_evidence(mind):
    m = mind.remember("Groceries run about $600 a month.", source="user")
    mind.file(m, "finance", source="agent")
    mind.file(m, "finance", source="agent")  # idempotent: no second evidence
    mind.unfile(m, "finance", source="user")
    kinds = [(e["kind"], e["note"]) for e in mind.evidence(m)]
    assert kinds == [("observe", None), ("file", "finance"), ("unfile", "finance")]


def test_topics_list_descriptions_not_contents(mind):
    mind.topic("finance", "Money: budgets, salary, taxes.")
    mind.remember("Groceries run about $600 a month.", source="user", topics=["finance"])
    assert mind.topics() == [{"name": "finance", "description": "Money: budgets, salary, taxes.",
                                  "memories": 1}]
    with pytest.raises(ValueError):
        mind.topic("Bad Name")


def test_unknown_topic_searches_only_the_general_topic(mind):
    d = mind.remember("Sam likes tea.", source="user")
    assert [h.id for h in mind.recall("tea", topics=["nope"])] == [d]


def test_keyword_only_hits_are_labelled_as_such(mind):
    mind.remember("zyzzyva alpha beta gamma delta epsilon", source="user")
    hits = mind.recall("zyzzyva", topics=[])
    assert hits and hits[0].via in ("keyword", "both")


def test_recall_fails_loudly_when_it_cannot_search(tmp_path):
    ok = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    ok.remember("Sam likes tea.", source="user")
    ok.close()

    def broken(texts):
        raise OSError("model file vanished")

    m = Mind(tmp_path / "m.db", encode=broken, model_tag="toy")
    with pytest.raises(RecallFailed, match="model file vanished"):
        m.recall("tea", topics=[])
    m.close()


def test_recall_refuses_to_mix_encoders(tmp_path):
    a = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    a.remember("Sam likes tea.", source="user")
    a.close()
    b = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="other-model")
    with pytest.raises(RecallFailed, match="lack a other-model embedding"):
        b.recall("tea", topics=[])
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


def test_the_general_topic_is_never_a_name_a_memory_is_filed_under(mind):
    # docs/primitives/TOPIC.md: a memory filed in no other topic lives in the general one.
    with pytest.raises(ValueError, match="general topic"):
        mind.remember("Sam likes tea.", source="user", topics=["general"])


def test_an_episode_is_never_recalled(mind):
    # docs/primitives/EPISODE.md: its words reach an agent only as the source of a fact.
    ep = mind.episode("s1", "atlas")
    ep.add_turn("t1", "user", "Atlas deploys to Fly.io from main.", 1.0)
    assert mind.recall("Where does atlas deploy?", topics=["atlas"]) == []
    fact = mind.remember("Atlas deploys to Fly.io from main.", source="user", topics=["atlas"],
                         drawn_from=["t1"])
    assert [h.id for h in mind.recall("Where does atlas deploy?", topics=["atlas"])] == [fact]


def test_an_episode_adds_the_same_turn_once_and_never_edits_it(mind):
    ep = mind.episode("s1")
    assert ep.add_turn("t1", "user", "We use ruff.", 1.0)
    assert not ep.add_turn("t1", "user", "We use ruff.", 1.0)
    with pytest.raises(sqlite3.IntegrityError, match="never edited"):
        mind.db.execute("UPDATE turns SET text='We use black.'")
    assert [t["text"] for t in ep.turns()] == ["We use ruff."]


def test_every_association_is_born_with_a_fact(mind):
    # docs/primitives/ASSOCIATION.md: no association is made between memories that already exist.
    a = mind.remember("Atlas deploys to Fly.io.", source="user", at=1.0)
    b = mind.remember("Fly.io secrets live in 1Password.", source="user", at=2.0)
    assert mind.strengthen([a, b], at=3.0) == 0
    assert mind.associations(a) == {} and mind.associations(b) == {}
    c = mind.remember("Atlas's Fly.io app is atlas-prod.", source="user", at=4.0, associate=[a])
    assert set(mind.associations(c, now=4.0)) == {a} and set(mind.associations(a, now=4.0)) == {c}


def test_an_association_strengthens_with_use_and_weakens_without_it_but_is_never_severed(mind):
    a = mind.remember("Atlas deploys to Fly.io.", source="user", at=0.0)
    b = mind.remember("Atlas's Fly.io app is atlas-prod.", source="user", at=0.0, associate=[a])
    born = mind.associations(b, now=0.0)[a]
    assert mind.strengthen([a, b], at=0.0) == 2
    assert mind.associations(b, now=0.0)[a] > born
    years = 50 * 365 * 86400.0
    assert 0 < mind.associations(b, now=years)[a] < born


def test_a_memory_reached_through_an_association_says_so_and_names_what_brought_it(mind):
    # docs/capabilities/RECALL.md.
    deploy = mind.remember("Atlas deploys to Fly.io from the main branch.", source="user")
    vault = mind.remember("The production database password lives in 1Password under Ops.",
                          source="user", associate=[deploy])
    hits = {h.id: h for h in mind.recall("How does atlas deploy?")}
    assert hits[deploy].brought_by is None
    assert hits[vault].brought_by == deploy and hits[vault].via == "association"


def test_an_association_never_makes_a_memory_visible(mind):
    # docs/primitives/ASSOCIATION.md: recall follows one only when both ends are searched.
    deploy = mind.remember("Atlas deploys to Fly.io from the main branch.", source="user")
    secret = mind.remember("The production database password is hunter2.", source="user",
                           topics=["ops"], associate=[deploy])
    assert secret not in {h.id for h in mind.recall("How does atlas deploy?")}
    assert secret in {h.id for h in mind.recall("How does atlas deploy?", topics=["ops"])}


def test_associations_from_an_unsearched_topic_leave_no_trace_in_what_comes_to_mind(mind):
    # docs/primitives/TOPIC.md: associations into an unsearched topic don't affect ranking, and
    # neither does strength flowing from one.
    deploy = mind.remember("Atlas deploys to Fly.io from the main branch.", source="user")
    vault = mind.remember("Its secrets are kept in 1Password.", source="user", associate=[deploy])
    before = [(h.id, h.brought_by) for h in mind.recall("How does atlas deploy?")]
    assert (vault, deploy) in before
    for i in range(12):
        mind.remember(f"Hidden note {i} about the vault.", source="user", topics=["secret"], associate=[vault])
    assert [(h.id, h.brought_by) for h in mind.recall("How does atlas deploy?")] == before


def test_a_version_2_mind_keeps_every_word_it_held_as_episodes_and_its_facts_as_memories(tmp_path):
    # Version 2 kept turns as memories in session.* namespaces. They move, word for word, into
    # their sessions' episodes, where facts can be drawn from them (docs/primitives/EPISODE.md).
    from pathlib import Path
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript((Path(__file__).parent / "fixtures" / "mind_v2.sql").read_text())
    old.executescript("""
        INSERT INTO namespaces VALUES (1, 'atlas', 'The atlas project', 0), (2, 'session.s1', '', 0),
                                      (3, 'finance', 'Money', 0);
        INSERT INTO memories(id, content, key, created_at) VALUES
            (1, 'We deploy atlas to Fly.io now.', 'cc:u1', 10), (2, 'Got it.', 'cc:a1', 11),
            (3, 'Rent is $1,900.', 'note-1', 12), (4, 'Sam prefers terse answers.', 'note-2', 13);
        INSERT INTO memory_namespaces VALUES (1, 1), (1, 2), (2, 1), (2, 2), (3, 3);
        INSERT INTO evidence(memory_id, kind, source, episode, ts) VALUES
            (1, 'observe', 'user', 's1', 10), (2, 'observe', 'agent', 's1', 11),
            (3, 'observe', 'user', NULL, 12), (4, 'observe', 'host_memory', 's1', 13);
        INSERT INTO judged VALUES (1, 'p', 20);
    """)
    old.close()
    m = Mind(path, encode=toy_encode, model_tag="toy")
    ep = m.episode("s1")
    assert [(t["key"], t["source"], t["text"]) for t in ep.turns()] == [
        ("cc:u1", "user", "We deploy atlas to Fly.io now."), ("cc:a1", "agent", "Got it.")]
    assert ep.place == "atlas"
    assert [r[0] for r in m.db.execute("SELECT content FROM memories ORDER BY id")] == [
        "Rent is $1,900.", "Sam prefers terse answers."]
    assert [t["name"] for t in m.topics()] == ["atlas", "finance"]
    assert m.visible(3, ["finance"]) and not m.visible(3) and m.visible(4)
    assert not ep.add_turn("cc:u1", "user", "We deploy atlas to Fly.io now.")   # read again: not twice
    m.close()
    assert list(tmp_path.glob("old.db.v2-*.bak"))                               # a copy was kept first
    # A runtime from before refuses the file rather than quietly filing turns as memories again.
    assert sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE name='scopes'").fetchone()
