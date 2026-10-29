"""Consolidate (docs/capabilities/CONSOLIDATE.md) and the life of an episode, with a scripted model."""

from __future__ import annotations

import json

import pytest

from tapestry import consolidate, episode, jobs, judge, unrecorded
from tapestry.mind import Mind
from tests.test_mind import toy_encode


class Scripted:
    """A model that answers each window with the next scripted answer, and keeps what it was shown."""
    prompt, model = "scripted", "scripted-model"

    def __init__(self, *answers):
        self.answers, self.shown = list(answers), []

    def draw(self, *, place, date, topics, known, turns):
        self.shown.append({"place": place, "known": known, "turns": turns})
        return self.answers.pop(0) if self.answers else {"facts": []}


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    yield m
    m.close()


def ended(mind, sid="s1", place="atlas", turns=(("user", "We deploy atlas to Fly.io now."), ("agent", "Got it."))):
    ep = mind.episode(sid, place)
    for i, (who, text) in enumerate(turns):
        ep.add_turn(f"{sid}:{i}", who, text, 100.0 + i)
    ep.end(200.0)
    return ep


def fact(content, *, said_by="user", turns=(1,), topics=("atlas",), **kw):
    return {"content": content, "said_by": said_by, "turns": list(turns), "topics": list(topics), **kw}


def memories(mind):
    return [r[0] for r in mind.db.execute("SELECT content FROM memories ORDER BY id")]


def test_a_conversation_that_taught_nothing_adds_nothing(mind):
    ep = ended(mind, turns=(("user", "Can you run the tests?"), ("agent", "All 40 pass.")))
    consolidate.consolidate(mind, Scripted({"facts": []}))
    assert memories(mind) == [] and ep.drawn_through == ep.turns()[-1]["seq"]


def test_a_drawn_fact_is_attributed_to_whoever_said_it(mind):
    # docs/primitives/MEMORY.md: attributed to whoever the claim came from, not whoever wrote it down.
    ended(mind)
    consolidate.consolidate(mind, Scripted({"facts": [
        fact("Atlas deploys to Fly.io.", said_by="user", turns=[1]),
        fact("Atlas has 40 tests.", said_by="user", turns=[2])]}))     # turn 2 is the agent's
    sources = dict(mind.db.execute("SELECT m.content, e.source FROM memories m JOIN evidence e"
                                   " ON e.memory_id = m.id AND e.kind = 'observe'"))
    assert sources == {"Atlas deploys to Fly.io.": "user", "Atlas has 40 tests.": "inference"}


def test_a_fact_that_cites_none_of_what_was_said_is_not_drawn(mind):
    ended(mind)
    stats = consolidate.consolidate(mind, Scripted({"facts": [fact("Atlas is written in Go.", turns=[]),
                                                              fact("Atlas is written in Rust.", turns=[9])]}))
    assert memories(mind) == [] and stats["dropped"] == 2


def test_a_fact_the_mind_already_holds_gets_the_conversation_as_evidence_not_a_second_copy(mind):
    # The manual path never waits: a noted fact is already there, and drawing treats it as held.
    noted = mind.remember("Atlas deploys to Fly.io.", source="agent", topics=["atlas"], at=50.0)
    ep = ended(mind)
    ep.add_recalled([noted], 100.0)
    consolidate.consolidate(mind, Scripted({"facts": [fact("Atlas deploys to Fly.io.", same_as=noted)]}))
    assert memories(mind) == ["Atlas deploys to Fly.io."]
    assert [(e["kind"], e["source"], e["episode"]) for e in mind.evidence(noted)][-1] == ("confirm", "user", "s1")


def test_a_correction_supersedes_through_the_same_checks_and_the_old_memory_stays_in_history(mind):
    old = mind.remember("Atlas deploys to Heroku.", source="user", topics=["atlas"], at=50.0)
    ep = ended(mind)
    ep.add_recalled([old], 100.0)
    consolidate.consolidate(mind, Scripted({"facts": [fact("Atlas deploys to Fly.io.", replaces=old)]}))
    new = mind.superseded_by(old)
    assert new is not None and mind.content(new) == "Atlas deploys to Fly.io."
    assert mind.evidence(old)[-1]["source"] == "inference"         # the replacing was the model's call
    assert mind.content(old) == "Atlas deploys to Heroku."         # never rewritten


def test_the_model_can_only_point_at_memories_it_was_shown(mind):
    hidden = mind.remember("Sam's salary is $105,000.", source="user", topics=["finance"], at=10.0)
    ended(mind)
    drawer = Scripted({"facts": [fact("Atlas deploys to Fly.io.", replaces=hidden, builds_on=[hidden])]})
    consolidate.consolidate(mind, drawer)
    assert hidden not in {i for i, _ in drawer.shown[0]["known"]}
    assert mind.superseded_by(hidden) is None
    new = next(r[0] for r in mind.db.execute("SELECT id FROM memories WHERE id != ?", (hidden,)))
    assert mind.associations(new) == {}


def test_facts_from_one_moment_are_linked_to_each_other_and_to_what_they_build_on(mind):
    # docs/primitives/ASSOCIATION.md: every association is born with a fact.
    base = mind.remember("Atlas runs on Fly.io.", source="user", topics=["atlas"], at=50.0)
    ep = ended(mind)
    ep.add_recalled([base], 100.0)
    consolidate.consolidate(mind, Scripted({"facts": [
        fact("Atlas's Fly app is atlas-prod.", builds_on=[base]),
        fact("Atlas deploys from main.")]}))
    a, b = [r[0] for r in mind.db.execute("SELECT id FROM memories WHERE id != ? ORDER BY id", (base,))]
    assert set(mind.associations(a)) == {base, b} and set(mind.associations(b)) == {a}


def test_associations_strengthen_only_at_a_moment_that_taught_something_new(mind):
    a = mind.remember("Atlas runs on Fly.io.", source="user", topics=["atlas"], at=50.0)
    b = mind.remember("Atlas's Fly app is atlas-prod.", source="user", topics=["atlas"], at=50.0, associate=[a])
    born = mind.associations(a, now=100.0)[b]
    quiet = ended(mind, sid="quiet")
    quiet.add_recalled([a, b], 100.0)
    consolidate.draw_episode(mind, quiet, Scripted({"facts": []}))
    assert mind.associations(a, now=100.0)[b] == pytest.approx(born)
    taught = ended(mind, sid="taught")
    taught.add_recalled([a, b], 100.0)
    consolidate.draw_episode(mind, taught, Scripted({"facts": [fact("Atlas deploys from main.")]}))
    assert mind.associations(a, now=101.0)[b] > born


def test_a_fact_is_filed_where_it_matters_and_narrowly_when_that_is_unclear(mind):
    # docs/primitives/TOPIC.md: in the general topic only when it matters in every context.
    ended(mind)
    consolidate.consolidate(mind, Scripted({"facts": [
        fact("Will prefers terse answers.", topics=["general"]),
        fact("Atlas deploys from main.", topics=[]),
        fact("Will's salary is $105,000.", topics=["finance"])],
        "new_topics": [{"name": "finance", "description": "Will's money."}]}))
    filed = {c: t for c, t in mind.db.execute(
        "SELECT m.content, (SELECT group_concat(t.name) FROM memory_topics x JOIN topics t ON t.id = x.topic_id"
        " WHERE x.memory_id = m.id) FROM memories m")}
    assert filed == {"Will prefers terse answers.": None, "Atlas deploys from main.": "atlas",
                     "Will's salary is $105,000.": "finance"}
    assert {"name": "finance", "description": "Will's money.", "memories": 1} in mind.topics()


def test_drawing_the_same_turns_twice_changes_nothing(mind):
    ep = ended(mind)
    answer = {"facts": [fact("Atlas deploys to Fly.io.")]}
    consolidate.draw_episode(mind, ep, Scripted(answer))
    before = mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0]
    mind.db.execute("UPDATE episodes SET drawn_through = 0")      # as if the run died before marking
    consolidate.draw_episode(mind, ep, Scripted(answer))
    assert memories(mind) == ["Atlas deploys to Fly.io."]
    assert mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == before
    assert consolidate.consolidate(mind, Scripted(answer)) == {"episodes": 0}


def test_only_turns_not_yet_drawn_are_read_again_when_a_session_resumes(mind):
    ep = ended(mind)
    consolidate.consolidate(mind, Scripted())
    ep.add_turn("s1:9", "user", "Also, atlas uses Postgres 16.", 300.0)
    ep.end(301.0)
    drawer = Scripted()
    consolidate.consolidate(mind, drawer)
    assert [t[2] for t in drawer.shown[0]["turns"]] == ["Also, atlas uses Postgres 16."]


def test_an_episode_never_goes_before_its_facts_are_drawn_and_goes_entirely_once_its_time_is_up(mind):
    # docs/primitives/EPISODE.md.
    ep = ended(mind)
    much_later = 200.0 + episode.KEEP * 2
    assert episode.expire(mind, now=much_later) == 0 and ep.turns()
    consolidate.consolidate(mind, Scripted({"facts": [fact("Atlas deploys to Fly.io.")]}))
    assert episode.expire(mind, now=200.0 + episode.KEEP / 2) == 0
    assert episode.expire(mind, now=much_later) == 1
    assert mind.db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
    assert b"We deploy atlas" not in mind.path.read_bytes()
    assert memories(mind) == ["Atlas deploys to Fly.io."]                 # what it taught stays
    assert mind.evidence(1)[0]["source"] == "user"                         # and so does who said it
    assert not ended(mind).turns()                                         # read again: not back


def test_a_session_still_in_a_conversation_is_not_drawn_from(mind):
    ep = mind.episode("live", "atlas")
    ep.add_turn("live:0", "user", "We deploy atlas to Fly.io now.", 10_000.0)
    assert episode.waiting(mind, now=10_060.0) == []
    assert [e.id for e in episode.waiting(mind, now=10_000.0 + episode.IDLE + 1)] == ["live"]


def test_a_drawing_that_fails_is_told_to_the_next_session_and_the_turns_wait(tmp_path, mind):
    ended(mind)

    class Broken(Scripted):
        def draw(self, **kw):
            raise RuntimeError("claude exited 1")
    behind = unrecorded.Ledger(tmp_path / "behind.jsonl")
    reopen = lambda: Mind(mind.path, encode=toy_encode, model_tag="toy")
    out = jobs.run(tmp_path, reopen, behind, jobs.Activity(tmp_path / "active"), judge=None, drawer=Broken())
    assert "drawing failed" in out and "claude exited 1" in jobs.behind_notice(behind.take())
    assert [e.id for e in episode.waiting(mind)] == ["s1"]


def test_conversations_leave_the_machine_only_for_a_model_the_owner_chose_to_draw_with(tmp_path, monkeypatch):
    # docs/interfaces/JOBS.md: the only thing jobs reach outside is a model the owner chose.
    monkeypatch.delenv(judge.PLUGIN_JUDGE, raising=False)
    monkeypatch.setattr(judge.shutil, "which", lambda name: "/usr/bin/claude")
    (tmp_path / "jev.key").write_text("k")
    assert judge.drawer(tmp_path) is None                 # a Jev key chooses Jev, which never reads conversations
    judge.choose(tmp_path, "none")
    assert judge.drawer(tmp_path) is None
    judge.choose(tmp_path, "claude")
    assert isinstance(judge.drawer(tmp_path), judge.ClaudeDrawer)


def test_claude_draws_with_every_customization_off_and_a_malformed_answer_is_a_failure():
    seen = []

    def run(argv, message):
        seen.append((argv, message))
        return json.dumps({"is_error": False, "modelUsage": {"claude-haiku": {}},
                           "structured_output": {"facts": [fact("Atlas deploys to Fly.io.")]}})
    d = judge.ClaudeDrawer(run=run)
    out = d.draw(place="atlas", date="Wednesday 2026-10-28", topics=[{"name": "atlas", "description": "The atlas project."}],
                 known=[(7, "Atlas runs on Fly.io.")], turns=[(1, "user", "We deploy atlas to Fly.io now.")])
    argv, message = seen[0]
    assert "--safe-mode" in argv and argv[argv.index("--tools") + 1] == ""
    assert "#7: Atlas runs on Fly.io." in message and "2026-10-28" in message and "[1] user: We deploy atlas" in message
    assert out["facts"][0]["content"] == "Atlas deploys to Fly.io." and d.model == "claude:claude-haiku"
    bad = judge.ClaudeDrawer(run=lambda argv, message: json.dumps({"is_error": False, "structured_output": {}}))
    with pytest.raises(RuntimeError, match="no list of facts"):
        bad.draw(place=None, date="Wednesday 2026-10-28", topics=[], known=[], turns=[(1, "user", "hi")])


def test_facts_from_different_turns_of_one_window_are_not_linked(mind):
    # A moment is what was said together: facts that share no turn share no moment.
    ended(mind, turns=(("user", "We deploy atlas to Fly.io now."), ("agent", "Got it."),
                       ("user", "Unrelated: my sister Priya is vegetarian.")))
    consolidate.consolidate(mind, Scripted({"facts": [fact("Atlas deploys to Fly.io.", turns=[1]),
                                                      fact("Priya is vegetarian.", turns=[3], topics=["priya"])]}))
    a, b = [r[0] for r in mind.db.execute("SELECT id FROM memories ORDER BY id")]
    assert mind.associations(a) == {} and mind.associations(b) == {}


def test_jev_is_asked_about_each_topics_description_never_its_memories():
    sent = []

    def send(body):
        sent.append(body)
        return {"provider_metadata": {"gateway": {"routing": {"finalProvider": judge.PROVIDER,
                                                              "modelAttempts": [{}]}}},
                "answers": {"c:0": {"type": "noul", "noul": 0.9}, "c:1": {"type": "noul", "noul": 0.1}}}
    router = judge.JevRouter(key="k", send=send)
    got = router.bearing("Is the guest room ready for Priya?",
                         [{"name": "priya", "description": "Will's sister"}, {"name": "atlas", "description": ""}])
    assert got == {"priya": 0.9, "atlas": 0.1}
    q = sent[0]["questions"]
    assert q["c:0"]["instructions"]["topic"] == "priya: Will's sister" and q["c:1"]["instructions"]["topic"] == "atlas: atlas"
    assert sent[0]["providerOptions"] == {"gateway": {"only": [judge.PROVIDER]}}
