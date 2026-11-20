"""Introspect (docs/capabilities/INTROSPECT.md), and the acts that tune what it finds
(docs/primitives/TOPIC.md, docs/interfaces/TOOLS.md)."""

from __future__ import annotations

import json

import pytest

from tapestry import change, introspect, tools, working
from tapestry.mind import Mind
from tests.test_mind import toy_encode


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    for name in ("atlas", "atlas/billing", "finance"):
        m.topic(name, f"What is known about {name}.")
    yield m
    m.close()


def _invoices(mind) -> dict[str, int]:
    """Five memories about the invoice run in atlas/billing, and one like them filed in finance."""
    ids = {f"run {day}": mind.remember(f"The invoice run for the vendor ledger closes on {day}.", source="user",
                                       topics=["atlas/billing"])
           for day in ("monday", "tuesday", "wednesday", "thursday", "friday")}
    ids["stray"] = mind.remember("The invoice run for the vendor ledger closes on saturday.", source="user",
                                 topics=["finance"])
    ids["budget"] = mind.remember("Groceries are budgeted at six hundred a month.", source="user", topics=["finance"])
    return ids


def _everything(mind) -> list:
    return [mind.db.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
            for t in ("memories", "memory_topics", "topics", "evidence", "associations")]


def test_introspecting_changes_nothing(mind):
    _invoices(mind)
    before = _everything(mind)
    for section in (None, *introspect.SECTIONS):
        introspect.digest(mind, section=section)
    assert _everything(mind) == before


def test_with_no_section_it_says_how_much_was_found_and_shows_no_memory(mind):
    _invoices(mind)
    found = introspect.digest(mind)
    assert found["memories"] == 7
    assert found["found"]["topics"]["filed_apart_from_what_it_resembles"] == 1
    assert "invoice" not in json.dumps(found)


def test_a_memory_filed_apart_from_everything_it_resembles_is_found(mind):
    ids = _invoices(mind)
    apart = introspect.digest(mind, section="topics")["filed_apart_from_what_it_resembles"]
    assert [(a["id"], a["in"], a["resembles"]) for a in apart] == [(ids["stray"], ["finance"], "atlas/billing")]


def test_a_memory_in_the_topic_above_the_one_it_resembles_is_filed_apart(mind):
    _invoices(mind)
    above = mind.remember("The invoice run for the vendor ledger closes on sunday.", source="user", topics=["atlas"])
    apart = introspect.digest(mind, section="topics")["filed_apart_from_what_it_resembles"]
    assert above in [a["id"] for a in apart]


def test_two_memories_that_say_the_same_are_found_once(mind):
    a = mind.remember("Sam's flight to Lisbon leaves at nine.", source="user")
    b = mind.remember("Sam's flight to Lisbon leaves at nine sharp.", source="agent")
    mind.remember("The boiler was serviced in March.", source="user")
    same = introspect.digest(mind, section="memories")["say_the_same"]
    assert [(s["a"]["id"], s["b"]["id"]) for s in same] == [(a, b)]


def test_looking_inside_a_topic_lists_nothing_filed_only_outside_it(mind):
    ids = _invoices(mind)
    mind.remember("Groceries are budgeted at six hundred a month now.", source="agent", topics=["finance"])
    inside = json.dumps(introspect.digest(mind, section="memories", topic="atlas"))
    assert "Groceries" not in inside
    assert "Groceries" in json.dumps(introspect.digest(mind, section="memories", topic="finance"))
    typical = introspect.digest(mind, section="topics", topic="atlas/billing")["typical"]
    assert {t["id"] for t in typical} <= set(ids.values()) - {ids["stray"], ids["budget"]}


def test_a_section_lists_only_as_many_as_asked_for_and_says_how_many_more(mind):
    for n in ("one", "two", "three"):
        mind.remember(f"The {n} long memory. " + "It goes on and on about the same thing. " * 15, source="user")
    found = introspect.digest(mind, section="memories", limit=2)
    assert len(found["long"]) == 2 and found["more"]["long"] == 1
    assert all(len(m["says"]) <= introspect.SHOWN for m in found["long"])


def test_an_unknown_section_is_refused(mind):
    with pytest.raises(ValueError):
        introspect.digest(mind, section="everything")


# -- tuning: filing a memory elsewhere ---------------------------------------

def _call(session, name, **args) -> dict:
    return json.loads(session.call(name, args))


def test_filing_a_memory_elsewhere_is_recorded_as_evidence(mind):
    ids = _invoices(mind)
    session = tools.Session(mind, ["finance"])
    done = _call(session, "tapestry_file", memories=[ids["stray"]], add=["atlas/billing"], remove=["finance"])
    assert (done["filed"], done["now_in_general"]) == (1, [])
    assert mind.memory(ids["stray"])["topics"] == ["atlas/billing"]
    kinds = [(e["kind"], e["source"], e["note"]) for e in mind.evidence(ids["stray"]) if e["kind"] != "observe"]
    assert ("file", "agent", "atlas/billing") in kinds and ("unfile", "agent", "finance") in kinds


def test_a_memory_taken_out_of_its_last_topic_is_reported_as_general(mind):
    ids = _invoices(mind)
    done = _call(tools.Session(mind, []), "tapestry_file", memories=[ids["budget"]], remove=["finance"])
    assert done["now_in_general"] == [ids["budget"]]


def test_filing_refuses_a_memory_the_session_cannot_see_as_it_refuses_one_that_does_not_exist(mind):
    ids = _invoices(mind)
    session = tools.Session(mind, [])
    hidden = _call(session, "tapestry_file", memories=[ids["budget"]], add=["atlas"])
    absent = _call(session, "tapestry_file", memories=[99999], add=["atlas"])
    assert "error" in hidden and hidden["error"].replace(str(ids["budget"]), "N") == absent["error"].replace("99999", "N")
    assert mind.memory(ids["budget"])["topics"] == ["finance"]


# -- tuning: moving a topic ---------------------------------------------------

def test_moving_a_topic_says_what_would_change_and_changes_nothing_until_confirmed(mind):
    _invoices(mind)
    session = tools.Session(mind, [])
    asked = _call(session, "tapestry_topics", action="move", topic="finance", to="atlas/finance")
    assert asked["moved"] is False and asked["would_move"]["renamed"] == {"finance": "atlas/finance"}
    assert {t["name"] for t in mind.topics()} == {"atlas", "atlas/billing", "finance"}


def test_a_moved_topic_takes_what_is_under_it_and_what_it_holds(mind):
    ids = _invoices(mind)
    mind.topic("ledgers", "Every ledger.")
    session = tools.Session(mind, ["atlas/billing"])
    _call(session, "tapestry_topics", action="move", topic="atlas", to="ledgers/atlas", confirmed=True)
    assert {t["name"] for t in mind.topics()} == {"ledgers", "ledgers/atlas", "ledgers/atlas/billing", "finance"}
    assert session.open == ["ledgers/atlas/billing"]
    assert mind.visible(ids["run monday"], ["ledgers"]) and mind.memory(ids["run monday"])["topics"] == ["ledgers/atlas/billing"]


def test_moving_onto_a_topic_that_exists_files_everything_there_and_the_old_one_goes(mind):
    ids = _invoices(mind)
    kept = working.write(mind, "Chase the vendor's quote.", topic="finance")
    change.move(mind, "finance", "atlas/billing")
    assert {t["name"] for t in mind.topics()} == {"atlas", "atlas/billing"}
    assert mind.memory(ids["budget"])["topics"] == ["atlas/billing"]
    assert ("unfile", "finance") in [(e["kind"], e["note"]) for e in mind.evidence(ids["budget"])]
    assert [w["id"] for w in working.in_view(mind, ["atlas/billing"])] == [kept["id"]]


def test_a_topic_cannot_move_under_itself_or_under_a_topic_that_is_not_there(mind):
    for to in ("atlas/billing/atlas", "nowhere/atlas"):
        with pytest.raises(ValueError):
            change.move(mind, "atlas", to)
    assert {t["name"] for t in mind.topics()} == {"atlas", "atlas/billing", "finance"}


def test_moving_says_when_conversations_happened_in_a_place_by_that_name(mind):
    mind.episode("s1", "atlas")
    assert mind.moving("atlas", "work")["a_place_by_that_name"] is True
    assert mind.moving("finance", "money")["a_place_by_that_name"] is False
