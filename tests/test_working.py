"""Working memory (docs/primitives/WORKING_MEMORY.md), through the module, the agent's tool and the viewer."""

from __future__ import annotations

import json

import pytest

from tapestry import consolidate, render, tools, viewer, working
from tapestry.mind import Mind
from tests.test_mind import toy_encode

DAY = 86400.0


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    for name in ("atlas", "atlas/billing", "finance"):
        m.topic(name, f"What is known about {name}.")
    yield m
    m.close()


def says(rows) -> list[str]:
    return [w["content"] for w in rows]


def test_a_working_memory_is_shown_whenever_its_topic_is_searched_and_nowhere_else(mind):
    working.write(mind, "Today: renew the passport.")
    working.write(mind, "Waiting on the atlas vendor's quote.", topic="atlas")
    working.write(mind, "Invoice run is half done.", topic="atlas/billing")
    assert says(working.in_view(mind)) == ["Today: renew the passport."]
    assert says(working.in_view(mind, ["finance"])) == ["Today: renew the passport."]
    assert says(working.in_view(mind, ["atlas/billing"])) == ["Today: renew the passport.", "Invoice run is half done."]
    assert says(working.in_view(mind, ["atlas"])) == ["Today: renew the passport.",
                                                      "Waiting on the atlas vendor's quote.", "Invoice run is half done."]


def test_a_working_memory_is_never_a_memory(mind):
    working.write(mind, "Waiting on the atlas vendor's quote.", topic="atlas")
    assert mind.recall("the atlas vendor's quote", topics=["atlas"], k=5) == []
    assert mind.memories(["atlas", "general"]) == []
    assert mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    assert mind.db.execute("SELECT count(*) FROM associations").fetchone()[0] == 0
    assert {t["name"]: t["memories"] for t in mind.topics()}["atlas"] == 0


def test_rewriting_replaces_what_it_says_and_keeps_nothing_of_what_it_said(mind):
    w = working.write(mind, "Today: renew the passport, call the plumber.")
    working.rewrite(mind, w["id"], "Today: call the plumber.")
    assert says(working.in_view(mind)) == ["Today: call the plumber."]
    assert "passport" not in "\n".join(mind.db.iterdump())


def test_rewriting_leaves_its_end_where_it_was_unless_a_new_one_is_given(mind):
    w = working.write(mind, "Today's list.", until=1000.0 + DAY, now=1000.0)
    assert working.rewrite(mind, w["id"], "Today's list, shorter.", now=2000.0)["until"] == 1000.0 + DAY
    assert working.rewrite(mind, w["id"], "This week's list.", until=1000.0 + 7 * DAY, now=3000.0)["until"] == 1000.0 + 7 * DAY
    assert working.rewrite(mind, w["id"], "The standing list.", until=None, now=4000.0)["until"] is None


def test_once_its_end_has_passed_it_is_gone_and_nothing_of_it_is_kept(mind):
    working.write(mind, "Today: renew the passport.", until=1000.0 + DAY, now=1000.0)
    keeps = working.write(mind, "Waiting on the plumber.", now=1000.0)
    assert len(working.in_view(mind, now=1000.0 + DAY - 1)) == 2
    assert says(working.in_view(mind, now=1000.0 + DAY)) == ["Waiting on the plumber."]
    with pytest.raises(KeyError):
        working.rewrite(mind, keeps["id"] - 1, "Back again.", now=1000.0 + DAY)
    working.write(mind, "Something new.", now=1000.0 + 2 * DAY)
    assert "passport" not in "\n".join(mind.db.iterdump())
    assert says(working.in_view(mind, now=1000.0 + 400 * DAY)) == ["Waiting on the plumber.", "Something new."]


def test_an_end_given_as_a_day_is_the_end_of_that_day_and_one_already_past_is_refused(mind):
    w = working.write(mind, "Today's list.", until="2030-01-02", now=working.end_of("2030-01-01T09:00"))
    assert working.end_of("2030-01-02T23:59") < w["until"] < working.end_of("2030-01-03T00:01")
    with pytest.raises(ValueError, match="already passed"):
        working.write(mind, "Yesterday's list.", until="2029-12-31", now=working.end_of("2030-01-01T09:00"))
    with pytest.raises(ValueError, match="an end is a day"):
        working.write(mind, "A list.", until="tonight")


def test_drawing_facts_from_a_conversation_never_touches_working_memory(mind):
    working.write(mind, "Waiting on the atlas vendor's quote.", topic="atlas")
    ep = mind.episode("s1", "atlas")
    ep.add_turn("t1", "user", "The atlas vendor's quote came in at $4,000, so stop waiting on it.", 1.0)
    ep.end()

    class Drawer:
        prompt, model = "scripted", "scripted-model"

        def draw(self, **kw):
            return {"facts": [{"content": "The atlas vendor quoted $4,000.", "said_by": "user", "turns": [1], "topics": ["atlas"]}]}
    assert consolidate.draw_episode(mind, ep, Drawer())["facts"] == 1
    assert says(working.in_view(mind, ["atlas"])) == ["Waiting on the atlas vendor's quote."]


def test_wherever_it_is_shown_it_says_how_long_ago_it_was_last_written(mind):
    w = working.write(mind, "Waiting on the plumber.", now=1000.0)
    assert "written 9 days ago" in render.working_line(working.get(mind, w["id"], now=1000.0 + 9 * DAY), 1000.0 + 9 * DAY)
    working.rewrite(mind, w["id"], "Still waiting on the plumber.", now=1000.0 + 9 * DAY)
    assert "written 2 hours ago" in render.working(working.in_view(mind, now=1000.0 + 9 * DAY + 7200), 1000.0 + 9 * DAY + 7200)


def test_the_agent_writes_rewrites_and_removes_working_memory_at_will(mind):
    session = tools.Session(mind, ["atlas"])
    call = lambda **args: json.loads(session.call("tapestry_working", args))
    assert "[w1 · general · written just now" in call(action="write", content="Today: renew the passport.", until="2999-01-01")["written"]
    assert "[w2 · atlas ·" in call(action="write", content="Waiting on the vendor.", topic="atlas")["written"]
    assert "ends 2999-01-01" in call(action="rewrite", id="w1", content="Today: call the plumber.")["rewritten"]
    assert "ends" not in call(action="rewrite", id="W1", content="Call the plumber sometime.", until="never")["rewritten"]
    assert [w.split("] ")[1] for w in call()["working"]] == ["Call the plumber sometime.", "Waiting on the vendor."]
    assert call(action="remove", id="w2") == {"removed": "w2"}
    assert len(call()["working"]) == 1


def test_a_working_memory_that_cant_be_written_or_found_says_so_and_changes_nothing(mind):
    session = tools.Session(mind, [])
    call = lambda **args: json.loads(session.call("tapestry_working", args))
    for bad in (dict(action="write", content="A list.", topic="nowhere"), dict(action="write", content=" "),
                dict(action="rewrite", id="w9", content="x"), dict(action="remove", id="nine"),
                dict(action="write", content="A list.", until="tonight")):
        assert "nothing was changed" in call(**bad)["error"]
    assert call()["working"] == []


def test_the_viewer_shows_the_working_memory_of_the_topics_picked_without_changing_the_mind(mind, tmp_path):
    working.write(mind, "Today: renew the passport.", now=1000.0)
    working.write(mind, "Invoice run is half done.", topic="atlas/billing", now=1000.0)
    before = mind.path.read_bytes()
    reading = Mind.reading(mind.path, encode=toy_encode, model_tag="toy")
    try:
        v = viewer.Viewer(reading)
        assert says(v.memories(["atlas"])["working"]) == ["Invoice run is half done."]
        assert says(v.memories(["general", "finance"])["working"]) == ["Today: renew the passport."]
        assert all("ago" in w["written"] for w in v.memories(["general", "atlas"])["working"])
    finally:
        reading.close()
    assert mind.path.read_bytes() == before


def test_a_topic_opened_or_named_mid_session_shows_what_it_keeps_in_view_then(mind):
    working.write(mind, "Today: renew the passport.")
    working.write(mind, "Invoice run is half done.", topic="atlas/billing")
    working.write(mind, "The budget review is on Friday.", topic="finance")
    session = tools.Session(mind, [])
    named = json.loads(session.call("tapestry_recall", {"query": "invoices", "topics": ["atlas"]}))
    assert [w.split("] ")[1] for w in named["working"]] == ["Invoice run is half done."]
    opened = json.loads(session.call("tapestry_topics", {"action": "open", "topic": "atlas"}))
    assert [w.split("] ")[1] for w in opened["working"]] == ["Invoice run is half done."]
    # Already in view: opening a topic under an open one, or naming an open one, shows nothing again.
    assert "working" not in json.loads(session.call("tapestry_topics", {"action": "open", "topic": "atlas/billing"}))
    assert "working" not in json.loads(session.call("tapestry_recall", {"query": "invoices", "topics": ["atlas"]}))
    assert "working" not in json.loads(session.call("tapestry_recall", {"query": "invoices"}))
