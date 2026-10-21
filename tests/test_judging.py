"""A model's judgment of two memories, and what recall shows when they conflict
(docs/capabilities/RECALL.md, docs/primitives/MEMORY.md, docs/interfaces/JOBS.md)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tapestry import belief, jobs, judge, render, unrecorded
from tapestry.mind import Mind
from tests.test_mind import toy_encode

PINK = "Sam's favorite color is pink."
BLUE = "Sam's favorite color is blue."


class FakeJudge:
    """Says 'conflict' for the pairs it is told about, and records what it was shown."""
    prompt, model = "conflicts_test", "fake-judge-1"

    def __init__(self, conflicting=(), fail=None):
        self.conflicting, self.fail, self.seen = set(conflicting), fail, []

    def conflicts(self, statement, others):
        self.seen.append((statement, [t for _, t in others]))
        if self.fail:
            raise self.fail
        return {i: "conflict" if frozenset((statement, t)) in self.conflicting else "no conflict"
                for i, t in others}


def pair(*texts):
    return {frozenset(texts)}


@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    yield m
    m.close()


def test_two_memories_a_model_finds_in_conflict_are_shown_together_and_neither_wins(mind):
    pink = mind.remember(PINK, source="user", at=1.0)
    blue = mind.remember(BLUE, source="user", at=2.0)
    assert jobs.judge_pending(mind, FakeJudge(pair(PINK, BLUE))) == 2
    hits = {h.id: h for h in mind.recall("favorite color", namespaces=[])}
    assert set(hits) == {pink, blue}
    assert hits[pink].conflicts == (blue,) and hits[blue].conflicts == (pink,)
    assert "CONFLICTS with #%d" % blue in render.line(hits[pink])
    assert mind.superseded_by(pink) is None and mind.superseded_by(blue) is None
    for h in hits.values():           # neither is believed as if nothing had happened
        assert h.belief.contradicted and h.belief.p < 0.5


def test_the_partner_comes_with_the_result_even_when_the_question_only_matches_one(mind):
    mind.remember(PINK, source="user", at=1.0)
    blue = mind.remember("Sam now says his favorite hue is blue, not pink, and always was.", source="user", at=2.0)
    jobs.judge_pending(mind, FakeJudge(pair(PINK, "Sam now says his favorite hue is blue, not pink, and always was.")))
    assert blue in {h.id for h in mind.recall("Sam's favorite color is pink", namespaces=[], k=1)}


def test_a_conflict_with_a_memory_this_lookup_cannot_see_leaves_no_trace(mind):
    pink = mind.remember(PINK, source="user", at=1.0)
    mind.remember(BLUE, source="user", at=2.0, namespaces=["finance"])
    jobs.judge_pending(mind, FakeJudge(pair(PINK, BLUE)))
    [hit] = mind.recall("favorite color", namespaces=[])
    assert hit.id == pink and hit.conflicts == () and "CONFLICT" not in render.line(hit)
    assert len(mind.recall("favorite color", namespaces=["finance"])) == 2


def test_what_the_model_was_asked_and_who_answered_is_kept_with_the_evidence(mind):
    pink = mind.remember(PINK, source="user", at=1.0)
    mind.remember(BLUE, source="web", at=2.0)
    jobs.judge_pending(mind, FakeJudge(pair(PINK, BLUE)))
    row = mind.db.execute("SELECT kind, source, trigger, reasoner, note FROM evidence"
                          " WHERE memory_id=? AND kind='contradict'", (pink,)).fetchone()
    assert row[0] == "contradict" and row[1] == "web"          # from the other memory's source
    assert row[2] == "judge:conflicts_test" and row[3] == "fake-judge-1" and row[4].startswith("conflicts with #")
    assert mind.db.execute("SELECT category FROM judgments").fetchall() == [("conflict",)]


def test_memories_that_do_not_conflict_are_left_alone(mind):
    mind.remember(PINK, source="user", at=1.0)
    mind.remember("Sam's favorite color is pink and so is his hat.", source="user", at=2.0)
    jobs.judge_pending(mind, FakeJudge())
    assert all(not h.conflicts and not h.belief.contradicted for h in mind.recall("favorite color", namespaces=[]))


def test_running_the_job_again_over_the_same_material_changes_nothing(mind):
    mind.remember(PINK, source="user", at=1.0)
    mind.remember(BLUE, source="user", at=2.0)
    judge_ = FakeJudge(pair(PINK, BLUE))
    jobs.judge_pending(mind, judge_)
    before = mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0], len(judge_.seen)
    assert jobs.judge_pending(mind, judge_) == 0
    assert (mind.db.execute("SELECT count(*) FROM evidence").fetchone()[0], len(judge_.seen)) == before


def test_each_pair_is_judged_once_whichever_memory_is_done_first(mind):
    mind.remember(PINK, source="user", at=1.0)
    mind.remember(BLUE, source="user", at=2.0)
    judge_ = FakeJudge()
    jobs.judge_pending(mind, judge_)
    assert len(judge_.seen) == 1                      # the second memory finds the pair already answered


def test_only_lasting_memories_are_compared_not_turns_of_a_conversation_or_replaced_ones(mind):
    old = mind.remember(PINK, source="user", at=1.0)
    mind.remember("Sam's favorite color is teal.", source="user", at=2.0, supersedes=old)
    mind.remember(BLUE, source="user", at=3.0, namespaces=["session.s1", "atlas"])
    judge_ = FakeJudge()
    assert jobs.judge_pending(mind, judge_) == 1      # only the teal note
    assert judge_.seen == []                           # and it has no lasting neighbour


def test_a_failure_partway_leaves_the_memory_for_next_time_and_the_next_session_is_told(tmp_path, mind):
    mind.remember(PINK, source="user", at=1.0)
    mind.remember(BLUE, source="user", at=2.0)
    behind = unrecorded.Ledger(tmp_path / "behind.jsonl")
    activity = jobs.Activity(tmp_path / "active")
    reopen = lambda: Mind(mind.path, encode=toy_encode, model_tag="toy")   # a job closes its mind
    out = jobs.run_judging(tmp_path, reopen, behind, activity, FakeJudge(fail=RuntimeError("503")))
    assert out.startswith("failed")
    assert mind.to_judge() != []
    notice = jobs.behind_notice(behind.take())
    assert "BACKGROUND WORK IS BEHIND" in notice and "503" in notice
    assert "BEHIND" not in jobs.behind_notice(behind.take())      # told once
    assert jobs.run_judging(tmp_path, reopen, behind, activity, FakeJudge()) == "judged 2 memories"


def test_no_job_runs_while_a_session_is_in_a_conversation(tmp_path, mind):
    mind.remember(PINK, source="user", at=1.0)
    behind, activity, judge_ = unrecorded.Ledger(tmp_path / "b"), jobs.Activity(tmp_path / "active"), FakeJudge()
    activity.touch("s1")
    reopen = lambda: Mind(mind.path, encode=toy_encode, model_tag="toy")
    assert "conversation is in progress" in jobs.run_judging(tmp_path, reopen, behind, activity, judge_)
    assert judge_.seen == [] and mind.to_judge() != []
    activity.end("s1")
    assert jobs.run_judging(tmp_path, reopen, behind, activity, judge_).startswith("judged")


def test_a_session_that_never_said_it_ended_stops_blocking_jobs(tmp_path):
    activity = jobs.Activity(tmp_path / "active", window=60)
    activity.touch("s1")
    assert activity.busy()
    old = time.time() - 120
    import os
    os.utime(next((tmp_path / "active").iterdir()), (old, old))
    assert not activity.busy()


def test_with_no_judge_configured_the_job_does_nothing_and_is_not_a_failure(tmp_path, monkeypatch, mind):
    monkeypatch.delenv("TAPESTRY_JEV_KEY_FILE", raising=False)
    behind = unrecorded.Ledger(tmp_path / "b")
    out = jobs.run_judging(tmp_path, lambda: mind, behind, jobs.Activity(tmp_path / "active"))
    assert "no judge configured" in out and behind.take() == []


def test_forgetting_one_of_two_conflicting_memories_ends_the_conflict_and_what_it_did_to_belief(mind):
    now = time.time()
    pink = mind.remember(PINK, source="user", at=now - 86400)
    blue = mind.remember(BLUE, source="user", at=now - 3600)
    before = mind.belief(pink, now=now).p
    jobs.judge_pending(mind, FakeJudge(pair(PINK, BLUE)))
    assert mind.belief(pink, now=now).p < before
    mind.forget(blue)
    assert mind.conflicts(pink) == [] and mind.belief(pink, now=now).p == pytest.approx(before)
    assert mind.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 0


def test_a_conflict_ends_when_one_of_the_two_is_superseded(mind):
    pink = mind.remember(PINK, source="user", at=1.0)
    blue = mind.remember(BLUE, source="user", at=2.0)
    jobs.judge_pending(mind, FakeJudge(pair(PINK, BLUE)))
    mind.remember("Sam's favorite color is blue, he confirmed.", source="user", at=4.0, supersedes=pink)
    assert mind.conflicts(blue) == []


# -- Jev ---------------------------------------------------------------------

def jev_response(ps, provider="typesafe-ai", attempts=1):
    return {"model": judge.MODEL,
            "provider_metadata": {"gateway": {"routing": {"finalProvider": provider,
                                                          "modelAttempts": [{}] * attempts}}},
            "answers": {f"c:{i}": {"type": "noul", "noul": p} for i, p in enumerate(ps)}}


def make_jev(tmp_path, ps, **kw):
    key = tmp_path / "key"
    key.write_text("secret\n")
    sent = []
    return judge.JevJudge(key, send=lambda body: sent.append(body) or jev_response(ps, **kw)), sent


def test_jev_is_asked_only_the_pair_and_only_of_typesafe(tmp_path):
    j, sent = make_jev(tmp_path, [0.95, 0.6, 0.1])
    got = j.conflicts(BLUE, [(1, PINK), (2, "Sam likes pink hats."), (3, "Atlas deploys to Fly.")])
    assert got == {1: "conflict", 2: "maybe", 3: "no conflict"}
    body = sent[0]
    assert body["model"] == "typesafe-ai/jev-1.13.0" and body["providerOptions"]["gateway"]["only"] == ["typesafe-ai"]
    assert json.dumps(body).count("Sam's favorite color is blue.") == 1 and "secret" not in json.dumps(body)


@pytest.mark.parametrize("kw", [{"provider": "openai"}, {"attempts": 2}])
def test_an_answer_from_anyone_but_a_single_typesafe_attempt_is_refused(tmp_path, kw):
    j, _ = make_jev(tmp_path, [0.99], **kw)
    with pytest.raises(RuntimeError):
        j.conflicts(BLUE, [(1, PINK)])


def test_an_answer_that_is_not_a_probability_is_not_read_as_no_conflict(tmp_path):
    j, _ = make_jev(tmp_path, [1.7])
    with pytest.raises(RuntimeError, match="no usable answer"):
        j.conflicts(BLUE, [(1, PINK)])


def test_the_judge_is_only_as_configured_as_its_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPESTRY_JEV_KEY_FILE", raising=False)
    assert judge.configured(tmp_path) is None
    (tmp_path / "jev.key").write_text("k")
    assert judge.configured(tmp_path).model == judge.MODEL


def test_belief_words_for_a_conflict_say_so():
    b = belief.read([{"id": 1, "kind": "observe", "source": "user", "episode": None, "strength": 1.0, "ts": 0},
                     {"id": 2, "kind": "contradict", "source": "user", "episode": None, "strength": 1.0, "ts": 1}], None, 2)
    assert "contradicted since" in belief.why(b, 2)


# -- Claude as the judge -------------------------------------------------------

def claude_out(verdicts, **extra):
    return json.dumps({"is_error": False, "total_cost_usd": 0.0005, "duration_api_ms": 2500,
                       "modelUsage": {"claude-haiku-5-5": {}},
                       "structured_output": {"answers": [{"id": i, "verdict": v} for i, v in verdicts]}, **extra})


def test_claude_judges_with_every_customization_off_so_it_cannot_trigger_tapestrys_own_hooks():
    seen = {}
    def run(argv, message):
        seen["argv"], seen["message"] = argv, message
        return claude_out([(1, "conflict"), (2, "no conflict")])
    j = judge.ClaudeJudge("haiku", run=run)
    assert j.conflicts(BLUE, [(7, PINK), (9, "Atlas deploys to Fly.")]) == {7: "conflict", 9: "no conflict"}
    assert "--safe-mode" in seen["argv"] and seen["argv"][seen["argv"].index("--tools") + 1] == ""
    assert BLUE in seen["message"] and PINK in seen["message"] and "Atlas deploys to Fly." in seen["message"]
    assert j.model == "claude:claude-haiku-5-5" and j.last["cost_usd"] == 0.0005


def test_a_verdict_claude_did_not_give_is_not_read_as_no_conflict():
    j = judge.ClaudeJudge(run=lambda argv, m: claude_out([(1, "conflict")]))
    with pytest.raises(RuntimeError, match="no usable verdict"):
        j.conflicts(BLUE, [(7, PINK), (9, "Sam likes tea.")])


def test_an_error_from_claude_is_a_failure_not_an_answer():
    j = judge.ClaudeJudge(run=lambda argv, m: json.dumps({"is_error": True, "result": "overloaded"}))
    with pytest.raises(RuntimeError, match="overloaded"):
        j.conflicts(BLUE, [(7, PINK)])


# -- who judges ----------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPESTRY_JEV_KEY_FILE", raising=False)
    monkeypatch.delenv(judge.PLUGIN_JUDGE, raising=False)
    monkeypatch.delenv(judge.PLUGIN_JEV_KEY, raising=False)
    monkeypatch.setattr(judge.shutil, "which", lambda name: "/bin/claude")
    return tmp_path


def test_memories_leave_the_machine_only_by_the_owners_act(home):
    # Nothing chosen and no key: nothing is sent, and Claude is never used unasked.
    assert judge.configured(home) is None
    (home / "jev.key").write_text("k")                  # putting a key in place is choosing Jev
    assert isinstance(judge.configured(home), judge.JevJudge)


def test_the_owners_choice_decides_whatever_else_is_on_the_machine(home):
    (home / "jev.key").write_text("k")
    judge.choose(home, "none")
    assert judge.configured(home) is None
    judge.choose(home, "claude")
    assert isinstance(judge.configured(home), judge.ClaudeJudge)
    judge.choose(home, "jev")
    assert isinstance(judge.configured(home), judge.JevJudge)


def test_choosing_says_where_memories_will_go(home):
    assert "TypeSafe" in judge.choose(home, "jev") and "Vercel" in judge.choose(home, "jev")
    assert "Claude" in judge.choose(home, "claude") and "never sent" in judge.choose(home, "none")
    with pytest.raises(ValueError):
        judge.choose(home, "gpt")


def test_a_judge_the_owner_chose_but_this_machine_cannot_run_is_never_swapped_for_another(home, monkeypatch):
    judge.choose(home, "jev")
    with pytest.raises(judge.JudgeUnavailable, match="no key"):
        judge.configured(home)
    judge.choose(home, "claude")
    monkeypatch.setattr(judge.shutil, "which", lambda name: None)
    (home / "jev.key").write_text("k")                  # a Jev key is there, but they chose Claude
    with pytest.raises(judge.JudgeUnavailable, match="claude command"):
        judge.configured(home)


def test_a_config_that_cannot_be_read_is_not_taken_as_no_choice(home):
    (home / "config.json").write_text("{not json")
    (home / "jev.key").write_text("k")
    with pytest.raises(judge.JudgeUnavailable, match="can't be read"):
        judge.configured(home)


def test_a_chosen_judge_that_cannot_run_is_reported_to_the_next_session(home, mind):
    judge.choose(home, "jev")
    behind = unrecorded.Ledger(home / "b")
    out = jobs.run_judging(home, lambda: mind, behind, jobs.Activity(home / "active"))
    assert out.startswith("failed") and "BACKGROUND WORK IS BEHIND" in jobs.behind_notice(behind.take())


def test_the_judge_command_records_the_choice_and_shows_where_memories_go(home, monkeypatch, capsys):
    from tapestry import cli
    monkeypatch.setenv("TAPESTRY_HOME", str(home))
    assert cli.main(["judge-with", "claude"]) == 0
    assert "Claude" in capsys.readouterr().out and judge.choice(home) == "claude"


# -- the plugin's own settings ---------------------------------------------------

def test_the_plugins_setting_is_the_choice_and_beats_the_command(home, monkeypatch):
    judge.choose(home, "none")
    monkeypatch.setenv(judge.PLUGIN_JUDGE, "claude")
    assert isinstance(judge.configured(home), judge.ClaudeJudge)
    monkeypatch.setenv(judge.PLUGIN_JUDGE, "")          # an empty setting is no setting
    assert judge.configured(home) is None


def test_a_jev_key_in_the_plugins_settings_counts_as_choosing_jev_and_is_never_a_file(home, monkeypatch):
    monkeypatch.setenv(judge.PLUGIN_JEV_KEY, "from-the-credential-store")
    j = judge.configured(home)
    assert isinstance(j, judge.JevJudge) and j._key == "from-the-credential-store"
    monkeypatch.setenv(judge.PLUGIN_JUDGE, "none")
    assert judge.configured(home) is None               # the explicit choice still wins


def test_a_plugin_setting_that_names_no_judge_is_a_failure_not_no_choice(home, monkeypatch):
    monkeypatch.setenv(judge.PLUGIN_JUDGE, "gpt")
    with pytest.raises(judge.JudgeUnavailable, match="unknown judge"):
        judge.configured(home)


def test_a_chosen_jev_with_no_key_anywhere_says_where_to_put_one(home, monkeypatch):
    monkeypatch.setenv(judge.PLUGIN_JUDGE, "jev")
    with pytest.raises(judge.JudgeUnavailable, match="plugin's settings"):
        judge.configured(home)


def test_the_plugin_declares_the_settings_the_judge_reads():
    cfg = json.loads((Path(__file__).parent.parent / ".claude-plugin" / "plugin.json").read_text())["userConfig"]
    assert set(cfg["judge"]["options"]) == {*judge.CHOICES, judge.PLUGIN_AUTO} and cfg["judge"]["default"] == judge.PLUGIN_AUTO
    assert cfg["jev_key"]["sensitive"] is True
    assert judge.PLUGIN_JUDGE == "CLAUDE_PLUGIN_OPTION_JUDGE" and judge.PLUGIN_JEV_KEY == "CLAUDE_PLUGIN_OPTION_JEV_KEY"


def test_the_plugins_default_setting_is_no_choice_so_the_command_and_a_key_still_count(home, monkeypatch):
    monkeypatch.setenv(judge.PLUGIN_JUDGE, judge.PLUGIN_AUTO)
    assert judge.configured(home) is None
    judge.choose(home, "claude")
    assert isinstance(judge.configured(home), judge.ClaudeJudge)
    judge.choose(home, "none")
    monkeypatch.setenv(judge.PLUGIN_JEV_KEY, "k")
    assert judge.configured(home) is None               # the command's choice of none still holds
    (home / "config.json").unlink()
    assert isinstance(judge.configured(home), judge.JevJudge)   # no choice, but a key


def test_claude_code_accepts_the_plugin_manifest():
    import shutil, subprocess
    if shutil.which("claude") is None:
        pytest.skip("needs the claude command to validate the manifest")
    root = Path(__file__).parent.parent
    out = subprocess.run(["claude", "plugin", "validate", str(root)], capture_output=True, text=True, timeout=60)
    assert "Found 1 error" not in out.stdout and "errors" not in out.stdout.split("Plugin directory checks")[0], out.stdout
