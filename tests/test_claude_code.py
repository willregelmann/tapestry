"""The Claude Code adapter and MCP server, with a toy encoder and a temp home."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tapestry import mcp_server
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
        proj = cc.project_namespace(cwd)
        if proj:
            m.namespace(proj)
        return m
    monkeypatch.setattr(cc, "open_mind", open_mind)
    return tmp_path


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "atlas"
    (r / ".git").mkdir(parents=True)
    (r / "src").mkdir()
    return r


def hook(event, **kw):
    out = cc.run_hook(json.dumps({"hook_event_name": event, **kw}))
    return json.loads(out) if out else None


def test_project_namespace_is_the_git_root(repo):
    assert cc.project_namespace(str(repo / "src")) == "atlas"
    assert cc.open_namespaces(str(repo / "src")) == ["atlas"]


def test_prompt_recalls_from_default_and_project_namespaces_only(repo, tmp_path):
    m = cc.open_mind(str(repo))
    m.remember("This project runs its tests with pytest.", source="user", namespaces=["atlas"])
    m.remember("This project runs its tests with vitest.", source="user", namespaces=["birch"])
    m.close()
    out = hook("UserPromptSubmit", prompt="How do I run the tests?", cwd=str(repo))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "pytest" in ctx and "vitest" not in ctx


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
        {"type": "assistant", "uuid": "s1", "isSidechain": True,
         "message": {"role": "assistant", "content": "subagent chatter"}},
        {"type": "summary", "summary": "x"},
    ]
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    first = cc.ingest(str(t), str(repo), "sess")
    assert first == {"remembered": 2, "already": 0}
    assert cc.ingest(str(t), str(repo), "sess") == {"remembered": 0, "already": 2}
    m = cc.open_mind(str(repo))
    rows = [(h.content, h.namespaces) for h in m.recall("Portland", namespaces=["atlas"])]
    sources = [r[0] for r in m.db.execute("SELECT source FROM evidence WHERE kind='observe' ORDER BY id")]
    m.close()
    assert sorted(rows) == [("I moved to Portland.", ("atlas",)), ("Noted: Portland.", ("atlas",))]
    assert sources == ["user", "agent"]


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
    assert m.db.execute("SELECT count(*) FROM memory_namespaces").fetchone()[0] == 0  # default
    m.close()


def test_mcp_server_speaks_the_protocol(repo, monkeypatch):
    s = mcp_server.Server(cwd=str(repo))
    init = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "tapestry"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
             ["result"]["tools"]]
    assert names == ["tapestry_recall", "tapestry_why", "tapestry_note", "tapestry_namespaces"]
    saved = s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "tapestry_note", "arguments": {"content": "Atlas deploys to Fly."}}})
    body = json.loads(saved["result"]["content"][0]["text"])
    assert body["namespaces"] == ["atlas"] and not saved["result"]["isError"]
    got = s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "tapestry_recall", "arguments": {"query": "Atlas deploys Fly"}}})
    assert "Fly" in json.loads(got["result"]["content"][0]["text"])["memories"]


def test_opened_namespaces_persist_per_project(repo):
    s = mcp_server.Server(cwd=str(repo))
    s._tools().mind.namespace("home-assistant", "The house automation setup.")
    s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "tapestry_namespaces", "arguments": {"action": "open", "namespace": "home-assistant"}}})
    assert cc.open_namespaces(str(repo)) == ["atlas", "home-assistant"]


def test_session_start_lists_namespaces_with_descriptions(repo):
    m = cc.open_mind(str(repo))
    m.namespace("finance", "Money: budgets, salary, taxes.")
    m.close()
    ctx = hook("SessionStart", cwd=str(repo))["hookSpecificOutput"]["additionalContext"]
    assert "finance: Money: budgets, salary, taxes." in ctx and "atlas (open)" in ctx
