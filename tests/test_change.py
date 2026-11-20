"""Change (docs/primitives/CHANGE.md): what the agent deliberately changes in a mind is recorded,
and can be undone."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tapestry import change, tools, viewer, working
from tapestry.mind import Mind
from tests.test_mind import toy_encode


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    for name in ("atlas", "atlas/billing", "finance"):
        m.topic(name, f"What is known about {name}.")
    yield m
    m.close()


@pytest.fixture
def ids(mind):
    return {"quote": mind.remember("The vendor's quote is due on Friday.", source="user", topics=["atlas"]),
            "budget": mind.remember("Groceries are budgeted at six hundred a month.", source="user", topics=["finance"]),
            "boiler": mind.remember("The boiler was serviced in March.", source="user")}


def _state(mind) -> dict:
    """Everything a change can touch, as it stands."""
    q = lambda sql: mind.db.execute(sql).fetchall()
    kept = q("SELECT id, topic_id, content FROM working_memories ORDER BY id") if mind.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='working_memories'").fetchone() else []
    return {"topics": q("SELECT id, name, description FROM topics ORDER BY id"),
            "filed": q("SELECT memory_id, topic_id FROM memory_topics ORDER BY 1, 2"),
            "links": [(s, d, round(v, 4)) for s, d, v in q("SELECT src, dst, strength FROM associations ORDER BY 1, 2")],
            "kept": kept}


def _call(s, name, **args) -> dict:
    return json.loads(s.call(name, args))


def _session(mind, sid="s1", opened=("atlas", "finance")) -> tools.Session:
    return tools.Session(mind, list(opened), episode=mind.episode(sid, None))


# -- recorded ---------------------------------------------------------------------------

def test_every_deliberate_change_is_recorded_with_who_when_where_and_why(mind, ids):
    s = _session(mind)
    _call(s, "tapestry_topics", action="describe", topic="finance", description="Sam's money.", reason="said only its name")
    _call(s, "tapestry_file", memories=[ids["boiler"]], add=["atlas"], reason="it is about the atlas office")
    _call(s, "tapestry_associate", memories=[ids["quote"], ids["budget"]], reason="the quote comes out of this budget")
    _call(s, "tapestry_topics", action="move", topic="finance", to="money", confirmed=True, reason="clearer name")
    log = change.log(mind)
    assert [c["did"] for c in reversed(log)] == [
        "described the topic finance again", "refiled 1 memory, into atlas",
        f"linked #{min(ids['quote'], ids['budget'])} and #{max(ids['quote'], ids['budget'])}",
        "moved the topic finance to money"]
    assert all(c["session"] == "s1" and c["by"] == "agent" and c["at"] > 0 and c["stands"] for c in log)
    assert [c["reason"] for c in reversed(log)][0] == "said only its name"


def test_a_change_that_is_not_made_is_not_recorded(mind, ids):
    with pytest.raises(ValueError):
        change.move(mind, "atlas", "nowhere/atlas")
    with pytest.raises(KeyError):
        change.file(mind, [99999], add=["atlas"])
    assert change.file(mind, [ids["quote"]], add=["atlas"]) is None
    assert change.describe(mind, "finance", "What is known about finance.") is None
    assert change.log(mind) == []


def test_a_recorded_change_is_never_rewritten(mind):
    change.describe(mind, "finance", "Sam's money.")
    with pytest.raises(sqlite3.DatabaseError):
        mind.db.execute("UPDATE changes SET detail='{}'")


def test_a_mind_nothing_was_changed_in_has_an_empty_record_and_is_still_only_read(mind, ids, tmp_path):
    reading = Mind.reading(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    assert change.log(reading) == []
    reading.close()


# -- undone -----------------------------------------------------------------------------

@pytest.mark.parametrize("act", ["describe", "create", "rename", "file", "link", "weaken", "merge"])
def test_undoing_a_change_puts_back_what_was_there_and_undoing_that_makes_it_again(mind, ids, act):
    working.write(mind, "Chase the quote.", topic="finance")
    covers = mind.remember("The quote covers the boiler.", source="user", associate=[ids["quote"]])
    mind.topic("work", "Work.")
    before = _state(mind)
    made = {"describe": lambda: [change.describe(mind, "finance", "Sam's money.")],
            "create": lambda: [change.describe(mind, "atlas/deploys", "How atlas ships.")],
            "rename": lambda: change.move(mind, "atlas", "work/atlas"),
            "file": lambda: [change.file(mind, [ids["budget"], ids["boiler"]], add=["atlas"], remove=["finance"])],
            "link": lambda: [change.associate(mind, ids["quote"], ids["budget"], "link", reason="same money")],
            "weaken": lambda: [change.associate(mind, ids["quote"], covers, "weaken", reason="unrelated")],
            "merge": lambda: change.move(mind, "finance", "atlas/billing")}[act]
    done = made()
    after = _state(mind)
    assert after != before
    undos = [change.undo(mind, c["change"]) for c in reversed(done)]
    assert _state(mind) == before
    for u in reversed(undos):
        change.undo(mind, u["change"])
    assert _state(mind) == after


def test_undoing_a_refiling_is_recorded_as_evidence_like_any_filing(mind, ids):
    made = change.file(mind, [ids["budget"]], add=["atlas"], remove=["finance"])
    change.undo(mind, made["change"])
    kinds = [(e["kind"], e["note"]) for e in mind.evidence(ids["budget"]) if e["kind"] != "observe"]
    assert kinds.count(("file", "finance")) == 2 and ("unfile", "atlas") in kinds


def test_an_undo_refuses_and_changes_nothing_where_the_mind_has_moved_on(mind, ids):
    first = change.describe(mind, "finance", "Sam's money.")
    change.describe(mind, "finance", "Sam's money: budgets and bills.")
    link = change.associate(mind, ids["quote"], ids["budget"], "link", reason="same money")
    mind.strengthen([ids["quote"], ids["budget"]])
    standing = _state(mind)
    for made in (first, link):
        with pytest.raises(change.Stale):
            change.undo(mind, made["change"])
    assert _state(mind) == standing


def test_a_change_is_undone_only_once(mind):
    made = change.describe(mind, "atlas", "The atlas office.")
    change.undo(mind, made["change"])
    with pytest.raises(change.Stale):
        change.undo(mind, made["change"])


def test_undoing_a_session_leaves_what_other_sessions_did_and_what_was_learned_since(mind, ids):
    tidy, later = _session(mind, "tidy"), _session(mind, "later")
    start = _state(mind)
    _call(tidy, "tapestry_topics", action="describe", topic="finance", description="Sam's money.")
    _call(tidy, "tapestry_file", memories=[ids["boiler"]], add=["atlas"])
    _call(tidy, "tapestry_associate", memories=[ids["quote"], ids["budget"]], reason="same money")
    learned = mind.remember("The quote came in at four thousand.", source="user", topics=["atlas"])
    _call(later, "tapestry_file", memories=[ids["budget"]], add=["atlas/billing"])
    done = _call(later, "tapestry_changes", action="undo", session="tidy")
    assert len(done["undone"]) == 3 and done["refused"] == []
    assert mind.memory(ids["boiler"])["topics"] == [] and mind.associations(ids["quote"]) == {}
    assert mind.memory(ids["budget"])["topics"] == ["atlas/billing", "finance"]
    assert mind.memory(learned)["topics"] == ["atlas"]
    assert _state(mind)["topics"] == start["topics"]


def test_undoing_a_session_passes_over_what_it_cannot_undo_and_says_why(mind, ids):
    tidy = _session(mind, "tidy")
    _call(tidy, "tapestry_topics", action="describe", topic="finance", description="Sam's money.")
    _call(tidy, "tapestry_file", memories=[ids["boiler"]], add=["atlas"])
    change.describe(mind, "finance", "Described again elsewhere.")
    done = change.undo_session(mind, "tidy")
    assert len(done["undone"]) == 1 and len(done["refused"]) == 1 and "described again" in done["refused"][0]["why"]
    assert mind.memory(ids["boiler"])["topics"] == []
    assert change.undo_session(mind, "tidy")["undone"] == []


# -- associations made by hand ------------------------------------------------------------

def test_a_link_made_by_hand_brings_one_memory_to_mind_from_the_other(mind, ids):
    s = _session(mind)
    made = _call(s, "tapestry_associate", memories=[ids["quote"], ids["budget"]], reason="the quote comes out of this budget")
    assert "change" in made
    assert ids["budget"] in mind.associations(ids["quote"]) and ids["quote"] in mind.associations(ids["budget"])
    weaker = mind.associations(ids["quote"])[ids["budget"]]
    _call(s, "tapestry_associate", memories=[ids["quote"], ids["budget"]], how="weaken", reason="less than I thought")
    assert mind.associations(ids["quote"])[ids["budget"]] < weaker


def test_an_association_needs_a_reason_and_memories_the_session_can_see(mind, ids):
    s = _session(mind, opened=["atlas"])
    assert "error" in _call(s, "tapestry_associate", memories=[ids["quote"], ids["boiler"]], reason=" ")
    hidden = _call(s, "tapestry_associate", memories=[ids["quote"], ids["budget"]], reason="same money")
    assert "error" in hidden and mind.associations(ids["quote"]) == {}
    assert "change" in _call(s, "tapestry_associate", memories=[ids["quote"], ids["budget"]], reason="same money",
                             topics=["finance"])
    assert "error" in _call(s, "tapestry_associate", memories=[ids["quote"], ids["budget"]], reason="again",
                            topics=["finance"])


# -- forgetting -------------------------------------------------------------------------

def test_forgetting_a_memory_takes_the_reasons_given_for_changing_it(mind, ids):
    change.file(mind, [ids["budget"]], add=["atlas"], reason="groceries at six hundred belong with atlas")
    change.associate(mind, ids["budget"], ids["quote"], "link", reason="six hundred covers the quote")
    kept = change.describe(mind, "finance", "Sam's money.", reason="said only its name")
    mind.forget(ids["budget"])
    log = {c["change"]: c for c in change.log(mind)}
    assert [c["reason"] for c in log.values() if c["change"] != kept["change"]] == ["", ""]
    assert log[kept["change"]]["reason"] == "said only its name"
    assert "six hundred" not in json.dumps(mind.db.execute("SELECT reason, detail FROM changes").fetchall())


def test_undoing_a_change_to_a_forgotten_memory_passes_over_it(mind, ids):
    made = change.file(mind, [ids["budget"], ids["boiler"]], add=["atlas"])
    mind.forget(ids["budget"])
    undone = change.undo(mind, made["change"])
    assert undone["no_longer_there"] == [ids["budget"]]
    assert mind.memory(ids["boiler"])["topics"] == []


def test_undoing_a_session_does_not_remake_what_that_session_itself_undid(mind, ids):
    tidy = _session(mind, "tidy")
    made = _call(tidy, "tapestry_file", memories=[ids["boiler"]], add=["atlas"])
    _call(tidy, "tapestry_changes", action="undo", change=made["change"])
    assert change.undo_session(mind, "tidy") == {"undone": [], "refused": []}
    assert mind.memory(ids["boiler"])["topics"] == []


def test_the_viewer_shows_the_changes_without_changing_the_mind(mind, ids, tmp_path):
    change.file(mind, [ids["boiler"]], add=["atlas"], reason="it is about the atlas office")
    undone = change.describe(mind, "finance", "Sam's money.")
    change.undo(mind, undone["change"])
    reading = Mind.reading(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    try:
        shown = viewer.Viewer(reading).changes()["changes"]
    finally:
        reading.close()
    assert [(c["did"], c["stands"]) for c in shown] == [
        ("described the topic finance again", True), ("described the topic finance again", False),
        ("refiled 1 memory, into atlas", True)]
    assert shown[-1]["reason"] == "it is about the atlas office" and shown[-1]["ago"]
