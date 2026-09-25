"""The Hermes adapter, outside Hermes: lifecycle, failure contract and quirks."""

from __future__ import annotations

import json

import pytest

from tapestry.hosts import hermes
from tests.test_mind import toy_encode


def toy_factory():
    return toy_encode, "toy"


@pytest.fixture
def provider(tmp_path):
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
    yield p
    p.shutdown()


def drain(p):
    p._writes.put(None)
    p._writer.join(timeout=10)


def test_turns_are_remembered_and_recalled(provider):
    provider.sync_turn("My favorite color is teal.", "Noted, teal it is.", session_id="s1")
    drain(provider)
    out = provider.prefetch("what is my favorite color")
    assert "My favorite color is teal." in out
    assert "match:" in out and "learned" in out
    assert provider._last_count >= 1


def test_trivial_prompts_skip_recall(provider):
    assert provider.prefetch("ok") == ""


def test_failed_init_is_loud_on_every_prefetch(tmp_path):
    def broken():
        raise OSError("model missing")
    p = hermes.TapestryProvider(encoder_factory=broken)
    p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
    out = p.prefetch("what is my favorite color")
    assert out.startswith("[MEMORY UNAVAILABLE") and "model missing" in out
    assert (tmp_path / "tapestry-initfail.log").exists()
    assert "error" in json.loads(p.handle_tool_call("tapestry_recall", {"query": "x"}))


def test_failed_recall_is_loud_not_empty(provider, monkeypatch):
    provider._reader.remember("Sam likes tea.", source="user")
    def boom(*a, **k):
        raise hermes.RecallFailed("disk I/O error")
    monkeypatch.setattr(provider._reader, "recall", boom)
    assert provider.prefetch("what does Sam like to drink").startswith("[MEMORY UNAVAILABLE")


def test_fence_sequences_are_neutralized_on_the_way_out(provider):
    text = "The sanitizer strips <memory-context> tags and [System note: lines."
    provider._reader.remember(text, source="user")
    out = provider.prefetch("what does the sanitizer strip")
    assert "<memory-context>" not in out and "[System note:" not in out
    assert "<`memory-context>" in out and "[System-note:" in out
    # Stored content is untouched: escaping is the adapter's job, not the core's.
    stored = provider._reader.db.execute("SELECT content FROM memories").fetchone()[0]
    assert stored == text


def test_note_is_recallable_immediately(provider):
    saved = json.loads(provider.handle_tool_call("tapestry_note", {"content": "Sam's dog is Biscuit."}))
    assert "saved" in saved
    got = json.loads(provider.handle_tool_call("tapestry_recall", {"query": "dog Biscuit"}))
    assert "Biscuit" in got["memories"]


def test_why_shows_evidence(provider):
    mid = json.loads(provider.handle_tool_call("tapestry_note",
                                               {"content": "Sam banks with Northwind."}))["saved"]
    why = json.loads(provider.handle_tool_call("tapestry_why", {"memory": mid}))
    assert why["evidence"][0]["kind"] == "observe" and why["evidence"][0]["source"] == "user"


def test_scopes_load_and_unload(provider):
    provider._reader.scope("atlas")
    loaded = json.loads(provider.handle_tool_call("tapestry_scopes", {"action": "load", "scope": "atlas"}))
    assert loaded["loaded"] == ["user", "atlas"]
    assert "error" in json.loads(provider.handle_tool_call(
        "tapestry_scopes", {"action": "unload", "scope": "user"}))


def test_host_memory_writes_become_low_weight_evidence(provider):
    provider.on_memory_write("add", "user", "Sam prefers short answers.")
    drain(provider)
    ev = provider._reader.db.execute("SELECT source FROM evidence").fetchall()
    assert ev == [("host_memory",)]


def test_subagents_do_not_write(tmp_path):
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize("s1", hermes_home=str(tmp_path), platform="cli", agent_context="subagent")
    p.sync_turn("hello there", "hi")
    assert p._writer is None and p._writes.empty()
    p.shutdown()


def test_tool_schemas_use_parameters():
    for t in hermes.TOOLS:
        assert "parameters" in t and "input_schema" not in t
