"""Recall's automatic path: highlights and hints (docs/capabilities/RECALL.md, docs/primitives/TOPIC.md)."""

import pytest

from tapestry import recall, render
from tapestry.mind import Mind
from tests.test_mind import toy_encode


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    yield m
    m.close()


def test_automatic_recall_shows_highlights_and_hints_never_everything_it_found(mind):
    for i in range(8):
        mind.remember(f"The garden plan for bed {i} grows tomatoes.", source="user")
    a = recall.before_turn(mind, "the garden plan grows tomatoes", [])
    shown = render.automatic(a)
    assert len(a.highlights) == recall.HIGHLIGHTS
    assert sum("grows tomatoes." in line for line in shown.splitlines()) == recall.HIGHLIGHTS
    assert a.more == {"": 8 - recall.HIGHLIGHTS} and "5 more memories match in the general topic" in shown


def test_before_a_turn_a_loose_match_is_never_a_highlight(mind):
    mind.remember("Sam keeps bees behind the shed and sells the honey in June.", source="user")
    a = recall.before_turn(mind, "when does Sam sell honey", [])
    assert a.highlights == [] and "behind the shed" not in render.automatic(a)
    assert recall.before_turn(mind, "quarterly tax filing deadline", []).highlights == []


def test_a_hint_says_where_more_is_and_following_it_shows_it(mind):
    mind.topic("finance", "Money: the household budget, groceries and bills.")
    mind.remember("Groceries run $600 a month.", source="user", topics=["finance"])
    a = recall.before_turn(mind, "how much do groceries run a month in the budget", [])
    shown = render.automatic(a)
    assert a.topics == ["finance"] and "finance" in shown and "$600" not in shown
    assert "$600" in render.block(mind.recall("how much do groceries run a month", topics=a.topics))


def test_an_unopened_topic_is_hinted_for_what_it_holds_never_for_its_description(mind):
    # A description that matches doesn't make an empty topic worth a hint; memories that match do,
    # under a description that doesn't (docs/primitives/TOPIC.md).
    mind.topic("boat", "The boat: engine, spark plugs and summer maintenance.")
    mind.topic("vault", "Old receipts.")
    moment = "boat engine spark plugs before summer"
    assert recall.before_turn(mind, moment, []).topics == []
    mind.remember("The boat engine needs new spark plugs before summer.", source="user", topics=["vault"])
    assert recall.before_turn(mind, moment, []).topics == ["vault"]


def test_a_hint_names_a_topic_and_never_what_it_holds(mind):
    # What matched in an unopened topic stays there: the hint is its name, nothing is highlighted
    # or counted from it, and nothing it says reaches what the agent is shown.
    mind.topic("vault", "Old receipts.")
    mind.remember("The boat engine needs new spark plugs before summer.", source="user", topics=["vault"])
    a = recall.before_turn(mind, "boat engine spark plugs before summer", [])
    shown = render.automatic(a)
    assert a.topics == ["vault"] and a.highlights == [] and a.more == {}
    assert "vault" in shown and "spark plugs" not in shown


def test_a_memory_an_opened_topic_already_shows_is_no_reason_to_hint_another(mind):
    mind.topic("vault", "Old receipts.")
    mind.remember("The boat engine needs new spark plugs before summer.", source="user", topics=["vault", "boat"])
    assert recall.before_turn(mind, "boat engine spark plugs before summer", ["boat"]).topics == []


def test_a_session_start_shows_a_few_highlights_about_its_place(mind):
    for i in range(6):
        mind.remember(f"Atlas decision {i}: deploy from the release branch.", source="user", topics=["atlas"])
    a = recall.session_start(mind, "Where things stand in the atlas project", ["atlas"])
    assert 0 < len(a.highlights) <= recall.HIGHLIGHTS
