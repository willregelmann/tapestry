"""The Claude Code adapter and MCP server, with a toy encoder and a temp home."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tapestry import cli, jobs, mcp_server, render
from tapestry.hosts import claude_code as cc
from tapestry.mind import Mind
from tests.test_mind import toy_encode


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAPESTRY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TAPESTRY_OWNER", "tester")

    def open_mind(cwd=None):
        cc.mind_path().parent.mkdir(parents=True, exist_ok=True)
        m = Mind(cc.mind_path(), encode=toy_encode, model_tag="toy")
        proj = cc.project_topic(cwd)
        if proj:
            m.topic(proj)
        return m
    monkeypatch.setattr(cc, "open_mind", open_mind)
    started = []
    monkeypatch.setattr(jobs, "start", lambda log: started.append(log))
    return started


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "atlas"
    (r / ".git").mkdir(parents=True)
    (r / "src").mkdir()
    return r


def hook(event, **kw):
    out = cc.run_hook(json.dumps({"hook_event_name": event, **kw}))
    return json.loads(out) if out else None


def test_project_topic_is_the_git_root(repo):
    assert cc.project_topic(str(repo / "src")) == "atlas"
    assert cc.open_topics(str(repo / "src")) == ["atlas"]


def test_prompt_recalls_from_default_and_project_topics_only(repo, tmp_path):
    m = cc.open_mind(str(repo))
    m.remember("This project runs its tests with pytest.", source="user", topics=["atlas"])
    m.remember("This project runs its tests with vitest.", source="user", topics=["birch"])
    m.close()
    out = hook("UserPromptSubmit", prompt="Which tool does this project run its tests with?", cwd=str(repo))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "pytest" in ctx and "vitest" not in ctx


def test_claude_code_recall_fits_the_same_budget_as_every_host(repo):
    # docs/interfaces/HOOKS.md: a behaviour that differs by host is a bug. Hermes budgets its recall.
    m = cc.open_mind(str(repo))
    for i in range(5):
        m.remember(f"Atlas deploy notes, part {i}: " + "atlas deploy notes " * 180,
                   source="user", topics=["atlas"])
    m.close()
    ctx = hook("UserPromptSubmit", prompt="Atlas deploy notes", cwd=str(repo)
               )["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) < render.PROMPT_BUDGET + 200
    assert "didn't fit; tapestry_recall shows them" in ctx


def test_a_transcript_that_could_not_be_recorded_is_reported_once_to_the_agent(repo, tmp_path):
    # docs/primitives/MEMORY.md: a moment that can't be recorded is reported, never silently lost.
    with pytest.raises(OSError):
        cli.main(["ingest", str(tmp_path / "missing.jsonl"), "--cwd", str(repo), "--session", "s1"])
    ctx = lambda: (hook("UserPromptSubmit", prompt="What did we decide?", cwd=str(repo)) or
                   {"hookSpecificOutput": {"additionalContext": ""}})["hookSpecificOutput"]["additionalContext"]
    assert "MEMORY GAP" in ctx()
    assert "MEMORY GAP" not in ctx()


def test_the_words_of_a_forgotten_fact_are_not_added_again_when_their_transcript_is_read_again(repo, tmp_path):
    # docs/primitives/EPISODE.md: forgetting takes the words a fact came from, and Stop, PreCompact
    # and SessionEnd each read the same transcript.
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-10-01T10:00:00Z",
                             "message": {"content": "My locker code is 4417."}}) + "\n")
    cc.ingest(str(t), str(repo), "s1")
    m = cc.open_mind(str(repo))
    mid = m.remember("Sam's locker code is 4417.", source="user", drawn_from=["cc:u1"])
    m.forget(mid)
    m.close()
    assert cc.ingest(str(t), str(repo), "s1") == {"added": 0, "already": 1}
    m = cc.open_mind(str(repo))
    assert m.episode("s1").turns() == []
    m.close()


def test_background_work_starts_only_when_a_session_ends_and_never_beside_a_conversation(repo, temp_home):
    # docs/interfaces/JOBS.md: a job never runs during a conversation in the same mind.
    hook("SessionStart", cwd=str(repo), session_id="s1")
    hook("UserPromptSubmit", prompt="What did we decide?", cwd=str(repo), session_id="s1")
    assert temp_home == [] and cc.activity().busy()
    hook("SessionEnd", cwd=str(repo), session_id="s1")
    assert len(temp_home) == 1 and not cc.activity().busy()


def test_background_work_that_failed_is_told_to_the_next_session_once(repo):
    cc.behind().add("judging memories failed (RuntimeError: 503)")
    ctx = lambda: hook("SessionStart", cwd=str(repo), session_id="s2")["hookSpecificOutput"]["additionalContext"]
    assert "BACKGROUND WORK IS BEHIND" in ctx() and "BACKGROUND WORK IS BEHIND" not in ctx()


def test_session_start_names_the_mind_it_attached_to(repo):
    ctx = hook("SessionStart", cwd=str(repo), session_id="s1")["hookSpecificOutput"]["additionalContext"]
    assert "'tester'" in ctx and str(cc.mind_path()) in ctx


def test_trivial_prompts_and_slash_commands_skip_recall(repo):
    assert hook("UserPromptSubmit", prompt="thanks!", cwd=str(repo)) is None
    assert hook("UserPromptSubmit", prompt="/clear", cwd=str(repo)) is None


def test_broken_mind_is_loud(repo, monkeypatch):
    def broken(cwd=None):
        raise OSError("model missing")
    monkeypatch.setattr(cc, "open_mind", broken)
    for event in ("SessionStart", "UserPromptSubmit"):
        ctx = hook(event, prompt="where do I live", cwd=str(repo))["hookSpecificOutput"]["additionalContext"]
        assert ctx.startswith("[MEMORY UNAVAILABLE") and "model missing" in ctx


def test_transcript_ingest_keeps_only_the_conversation(repo, tmp_path):
    lines = [
        {"type": "user", "uuid": "u1", "timestamp": "2026-09-25T10:00:00Z",
         "message": {"role": "user", "content": "I moved to Portland."}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-09-25T10:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Noted: Portland."}, {"type": "tool_use", "name": "x"}]}},
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "file contents"}]}},
        {"type": "user", "uuid": "u3", "message": {"role": "user",
                                                    "content": "<system-reminder>ignore</system-reminder>"}},
        {"type": "user", "uuid": "u4", "message": {"role": "user",
                                                    "content": "<bash-stdout>total 48</bash-stdout><bash-stderr></bash-stderr>"}},
        {"type": "user", "uuid": "u5", "message": {"role": "user", "content": "<bash-input>ls -la</bash-input>"}},
        {"type": "system", "uuid": "c0", "subtype": "compact_boundary"},
        {"type": "user", "uuid": "c1", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
         "message": {"role": "user", "content": "This session is being continued from a previous conversation."}},
        {"type": "assistant", "uuid": "s1", "isSidechain": True,
         "message": {"role": "assistant", "content": "subagent chatter"}},
        {"type": "summary", "summary": "x"},
    ]
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    assert cc.ingest(str(t), str(repo), "sess") == {"added": 2, "already": 0}
    assert cc.ingest(str(t), str(repo), "sess") == {"added": 0, "already": 2}
    m = cc.open_mind(str(repo))
    ep = m.episode("sess")
    turns = [(t["source"], t["text"]) for t in ep.turns()]
    memories = m.db.execute("SELECT count(*) FROM memories").fetchone()[0]
    m.close()
    assert turns == [("user", "I moved to Portland."), ("agent", "Noted: Portland.")]
    assert ep.place == "atlas"
    assert memories == 0    # a conversation is an episode, never a memory


def _transcript(tmp_path, text):
    uuid = f"u-{abs(hash(text))}"
    t = tmp_path / f"t-{uuid}.jsonl"
    t.write_text(json.dumps({"type": "user", "uuid": uuid, "timestamp": "2026-10-14T10:00:00Z",
                             "message": {"role": "user", "content": text}}) + "\n")
    return str(t)


class Drawer:
    """Stands in for the model: every user turn is a fact the user stated, filed where it was said."""
    prompt, model = "fake_draw", "fake"

    def draw(self, *, place, date, topics, known, turns):
        return {"facts": [{"content": text, "said_by": "user", "turns": [i],
                           "topics": [place] if place else ["general"]}
                          for i, who, text in turns if who == "user"]}


def test_a_later_session_in_the_project_recalls_what_an_earlier_one_taught(repo, tmp_path):
    hook("SessionStart", cwd=str(repo), session_id="first")
    cc.ingest(_transcript(tmp_path, "Atlas deploys to Fly.io from main."), str(repo), "first")
    hook("SessionEnd", cwd=str(repo), session_id="first")
    # Nothing is drawn yet, and an episode is never recalled.
    assert hook("UserPromptSubmit", prompt="Atlas deploys to where?", cwd=str(repo),
                session_id="peek") is None
    hook("SessionEnd", cwd=str(repo), session_id="peek")
    assert "drawing: {'episodes': 1, 'facts': 1" in jobs.run(cc.home(), cc.open_mind, cc.behind(), cc.activity(),
                                                             judge=None, drawer=Drawer())
    ctx = hook("UserPromptSubmit", prompt="Atlas deploys to where?", cwd=str(repo),
               session_id="second")["hookSpecificOutput"]["additionalContext"]
    assert "Fly.io" in ctx and "the user said it" in ctx
    other = tmp_path / "birch"
    (other / ".git").mkdir(parents=True)
    out = hook("UserPromptSubmit", prompt="Atlas deploys to where?", cwd=str(other), session_id="third")
    assert "Fly.io" not in json.dumps(out)


def test_a_session_starts_knowing_where_things_stand_in_its_place(repo):
    # docs/capabilities/RECALL.md: a session never starts as a stranger by default.
    m = cc.open_mind(str(repo))
    m.remember("Atlas is midway through moving its deploys from Heroku to Fly.io.", source="user",
               topics=["atlas"], volatility="project")
    m.remember("Birch has no tests yet.", source="user", topics=["birch"])
    m.close()
    ctx = hook("SessionStart", cwd=str(repo), session_id="s1")["hookSpecificOutput"]["additionalContext"]
    assert "where things stand" in ctx and "Heroku to Fly.io" in ctx and "Birch" not in ctx
    m = cc.open_mind(str(repo))
    assert m.episode("s1").recalled_between(0, float("inf"))   # what was in mind is part of the episode
    m.close()


def test_conversations_waiting_for_a_model_nobody_chose_are_told_to_the_next_session(repo, tmp_path):
    # docs/interfaces/JOBS.md: with no model chosen, nothing leaves the machine, and the next
    # session is told what is waiting.
    cc.ingest(_transcript(tmp_path, "We use ruff for lint."), str(repo), "s1")
    hook("SessionEnd", cwd=str(repo), session_id="s1")
    ctx = hook("SessionStart", cwd=str(repo), session_id="s2")["hookSpecificOutput"]["additionalContext"]
    assert "1 past conversation is waiting" in ctx and "tapestry judge-with claude" in ctx


def test_host_memory_writes_are_evidence(repo, tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    mem = fake_home / ".claude" / "projects" / "p" / "memory" / "note.md"
    mem.parent.mkdir(parents=True)
    mem.write_text("The user prefers terse answers.")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    hook("PostToolUse", tool_name="Write", tool_input={"file_path": str(mem)}, cwd=str(repo))
    hook("PostToolUse", tool_name="Write", tool_input={"file_path": str(repo / "README.md")},
         cwd=str(repo))
    m = cc.open_mind(str(repo))
    assert m.db.execute("SELECT e.source FROM evidence e").fetchall() == [("host_memory",)]
    assert m.db.execute("SELECT count(*) FROM memory_topics").fetchone()[0] == 0  # default
    m.close()


def test_mcp_server_speaks_the_protocol(repo, monkeypatch):
    s = mcp_server.Server(cwd=str(repo))
    init = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "tapestry"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
             ["result"]["tools"]]
    assert set(names) == {"tapestry_recall", "tapestry_why", "tapestry_note", "tapestry_record_answer",
                          "tapestry_forget", "tapestry_topics"}
    saved = s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "tapestry_note", "arguments": {"source": "user", "content": "Atlas deploys to Fly."}}})
    body = json.loads(saved["result"]["content"][0]["text"])
    assert body["topics"] == ["atlas"] and not saved["result"]["isError"]
    got = s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "tapestry_recall", "arguments": {"query": "Atlas deploys Fly"}}})
    assert "Fly" in json.loads(got["result"]["content"][0]["text"])["memories"]


def test_opened_topics_persist_per_project(repo):
    s = mcp_server.Server(cwd=str(repo))
    s._tools().mind.topic("home-assistant", "The house automation setup.")
    s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "tapestry_topics", "arguments": {"action": "open", "topic": "home-assistant"}}})
    assert cc.open_topics(str(repo)) == ["atlas", "home-assistant"]


def test_session_start_lists_topics_with_descriptions(repo):
    m = cc.open_mind(str(repo))
    m.topic("finance", "Money: budgets, salary, taxes.")
    m.close()
    ctx = hook("SessionStart", cwd=str(repo))["hookSpecificOutput"]["additionalContext"]
    assert "finance: Money: budgets, salary, taxes." in ctx and "atlas (open)" in ctx


def test_a_note_carries_its_session_and_is_linked_to_what_else_was_noted_in_that_moment(repo, monkeypatch):
    # docs/primitives/ASSOCIATION.md: a fact is linked to the others from the same moment.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    s = mcp_server.Server(cwd=str(repo))
    note = lambda text: json.loads(s._tools().call("tapestry_note", {"source": "user", "content": text}))["saved"]
    a, b = note("Atlas deploys to Fly.io."), note("Atlas's Fly app is atlas-prod.")
    mind = s._tools().mind
    assert [e["episode"] for e in mind.evidence(a)] == ["s1", "s1"]   # observe, and filing under atlas
    assert set(mind.associations(b)) == {a}
