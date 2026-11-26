"""Readings: sources the agent chose, read by Tapestry (docs/primitives/EPISODE.md,
docs/capabilities/CONSOLIDATE.md, docs/interfaces/TOOLS.md), with a scripted model."""

from __future__ import annotations

import json

import pytest

from tapestry import consolidate, episode, judge, sources, tools
from tapestry.mind import Mind
from tests.test_mind import toy_encode

ADDRESS = "https://example.org/hives"
PAGE = {"title": "Keeping bees", "url": ADDRESS,
        "sections": [("Hives", "A Langstroth hive has movable frames."),
                     ("Swarms", "A colony swarms when it outgrows its hive.")]}


class Reader:
    """A model that answers each window of a reading with the next scripted list of facts."""
    prompt, model = "scripted", "scripted-model"

    def __init__(self, *answers):
        self.answers, self.shown = list(answers), []

    def draw(self, **kw):
        raise AssertionError("a reading is never drawn as a conversation")

    def read(self, *, about, title, date, known, passages):
        self.shown.append({"about": about, "title": title, "known": known, "passages": passages})
        return self.answers.pop(0) if self.answers else []


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    m.topic("bees", "How to keep bees: hives, swarms and honey.")
    yield m
    m.close()


def read(mind, page=PAGE, *, session="s1", topic="bees", at=100.0):
    mind.episode(session, None).end(at)
    return episode.Episode.read(mind, session, address=page["url"], title=page["title"], topic=topic,
                                passages=page["sections"], at=at)


def memories(mind):
    return [r[0] for r in mind.db.execute("SELECT content FROM memories ORDER BY id")]


def test_a_readings_words_belong_to_its_source_never_to_the_user_or_the_agent(mind):
    ep = read(mind)
    assert {t["source"] for t in ep.turns()} == {episode.SOURCE}
    assert ep.reading and ep.address == ADDRESS and ep.topic == "bees"
    with pytest.raises(ValueError):
        ep.add_turn("more", "user", "Bees like clover.")


def test_asking_for_the_same_source_twice_in_a_session_gives_one_reading(mind):
    assert read(mind).id == read(mind).id
    assert mind.db.execute("SELECT count(*) FROM turns").fetchone()[0] == 2


def test_a_reading_waits_until_the_session_that_asked_for_it_has_ended(mind):
    live = mind.episode("live", None)
    live.add_turn("live:0", "user", "Learn about bees.", 10_000.0)
    ep = episode.Episode.read(mind, "live", address=ADDRESS, title="Keeping bees", topic="bees",
                              passages=PAGE["sections"], at=10_001.0)
    assert ep.id not in [e.id for e in episode.waiting(mind, now=10_060.0)]
    live.end(10_100.0)
    assert ep.id in [e.id for e in episode.waiting(mind, now=10_110.0)]


def test_a_reading_is_drawn_in_full_and_a_long_source_leaves_as_much_for_its_length(mind):
    # docs/capabilities/CONSOLIDATE.md: no gist budget, and windows small enough that length counts.
    long = {**PAGE, "sections": [(f"Part {i}", "Bees. " * 1200) for i in range(4)]}   # ~7k chars each
    word = lambda n: "".join("abcdefghij"[int(d)] for d in f"{n:03d}")     # the toy encoder reads letters
    reader = Reader(*[[{"content": f"Hive {word(w * 30 + i)}x keeps {word(i)}y comb near {word(w)}z frames.",
                        "passages": [1]} for i in range(30)] for w in range(4)])
    read(mind, long)
    consolidate.consolidate(mind, reader)
    assert len(reader.shown) == 4                                  # one window per part, not one for all
    assert len(memories(mind)) == 120                              # every fact kept, no budget


def test_what_a_reading_says_is_its_sources_claim_filed_where_it_was_read_for_and_cites_it(mind):
    read(mind)
    consolidate.consolidate(mind, Reader([{"content": "A Langstroth hive has movable frames.", "passages": [1]}]))
    (mid,) = [r[0] for r in mind.db.execute("SELECT id FROM memories")]
    first = mind.evidence(mid)[0]
    assert first["source"] == "web" and ADDRESS in first["note"] and "Keeping bees" in first["note"]
    assert [r[0] for r in mind.db.execute("SELECT t.name FROM memory_topics mt JOIN topics t ON t.id = mt.topic_id"
                                          " WHERE mt.memory_id=?", (mid,))] == ["bees"]
    assert mind.recall("Langstroth hive frames", topics=["bees"], k=1)[0].id == mid


def test_a_reading_never_supersedes_what_the_mind_holds(mind):
    old = mind.remember("A Langstroth hive has fixed frames.", source="user", topics=["bees"])
    read(mind)
    consolidate.consolidate(mind, Reader([{"content": "A Langstroth hive has movable frames.", "passages": [1],
                                           "replaces": old, "supersedes": old}]))
    assert mind.superseded_by(old) is None
    assert len(memories(mind)) == 2                                  # both claims stay current


def test_evidence_still_names_the_source_once_the_reading_has_gone(mind):
    read(mind)
    consolidate.consolidate(mind, Reader([{"content": "A colony swarms when it outgrows its hive.", "passages": [2]}]))
    assert episode.expire(mind, now=100.0 + episode.KEEP * 2) >= 1
    assert mind.db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
    assert ADDRESS in mind.evidence(1)[0]["note"]


def test_a_source_said_again_confirms_what_the_mind_holds_rather_than_copying_it(mind):
    held = mind.remember("A Langstroth hive has movable frames.", source="user", topics=["bees"])
    read(mind)
    consolidate.consolidate(mind, Reader([{"content": "A Langstroth hive has movable frames.", "passages": [1]}]))
    assert len(memories(mind)) == 1
    confirms = [e for e in mind.evidence(held) if e["kind"] == "confirm"]
    assert len(confirms) == 1 and confirms[0]["source"] == "web" and ADDRESS in confirms[0]["note"]


# -- the tool (docs/interfaces/TOOLS.md) -------------------------------------------------------

@pytest.fixture
def session(mind):
    return tools.Session(mind, [], place="bees", episode=mind.episode("s1", "bees"))


def call(session, **args):
    return json.loads(session.call("tapestry_read", args))


def test_the_read_tool_reads_now_and_says_nothing_is_in_memory_yet(session, mind, monkeypatch):
    monkeypatch.setattr(sources, "read", lambda url: PAGE)
    got = call(session, url=ADDRESS)
    assert got["read"] == "Keeping bees" and got["passages"] == 2 and got["topic"] == "bees"
    assert "recall won't find it" in got["note"]
    assert memories(mind) == []
    assert episode.Episode._load(mind, got["reading"]).reading


def test_a_source_that_cant_be_read_says_so_when_asked_and_nothing_waits(session, mind, monkeypatch):
    def unreadable(url):
        raise sources.Unreadable("couldn't fetch it")
    monkeypatch.setattr(sources, "read", unreadable)
    got = call(session, url=ADDRESS)
    assert "couldn't fetch it" in got["error"] and "nothing was read" in got["error"]
    assert mind.db.execute("SELECT count(*) FROM episodes WHERE kind='reading'").fetchone()[0] == 0


def test_a_reading_is_for_a_topic_that_exists(session, mind, monkeypatch):
    monkeypatch.setattr(sources, "read", lambda url: PAGE)
    assert "no topic" in call(session, url=ADDRESS, topic="wasps")["error"]
    assert call(session, url=ADDRESS, topic="general")["topic"] == "general"


def test_a_source_not_on_the_web_is_unreadable_and_never_empty():
    with pytest.raises(sources.Unreadable):
        sources.read("file:///etc/passwd")
    with pytest.raises(sources.Unreadable):
        sources.read("not an address")


def test_a_long_section_is_split_at_paragraphs_and_nothing_is_cut():
    text = "\n".join(f"Paragraph {i}. " + "word " * 300 for i in range(20))
    parts = sources._split([("Body", text)])
    assert len(parts) > 1 and all(len(t) <= sources.PASSAGE for _, t in parts)
    assert "".join(t.replace("\n", "") for _, t in parts) == text.replace("\n", "")


def test_both_drawers_can_read():
    assert hasattr(judge.ClaudeDrawer, "read") and hasattr(judge.ApiDrawer, "read")
