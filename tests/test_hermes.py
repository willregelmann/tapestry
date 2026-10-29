"""The Hermes adapter, outside Hermes: lifecycle, failure contract and quirks."""

from __future__ import annotations

import json
import sqlite3

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
    assert p._store.flush(timeout=10)


def _turns(p, sid="s1"):
    return [(t["source"], t["text"]) for t in p._reader.episode(sid).turns()]


def test_turns_go_to_the_sessions_episode_and_are_never_recalled(provider):
    # docs/primitives/EPISODE.md: an episode is never a memory, and is never recalled.
    provider.sync_turn("My favorite color is teal.", "Noted, teal it is.", session_id="s1")
    drain(provider)
    assert _turns(provider) == [("user", "My favorite color is teal."), ("agent", "Noted, teal it is.")]
    assert provider._reader.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
    assert provider.prefetch("what is my favorite color") == ""


def test_what_was_recalled_into_a_moment_is_part_of_the_episode(provider):
    mid = _note(provider, "Sam's favorite color is teal.")
    assert "teal" in provider.prefetch("what is Sam's favorite color")
    drain(provider)
    assert provider._reader.episode("s1").recalled_between(0, float("inf")) == [mid]


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


def test_a_turn_that_could_not_be_recorded_is_reported_once_at_the_next_prefetch(provider, monkeypatch):
    # docs/primitives/MEMORY.md: a moment that can't be recorded is reported, never silently lost.
    from tapestry.mind import Mind
    def boom(self, *a, **k):
        raise sqlite3.OperationalError("disk is full")
    from tapestry.episode import Episode
    monkeypatch.setattr(Episode, "add_turn", boom)
    provider.sync_turn("My locker code is 4417.", "Got it.", session_id="s1")
    drain(provider)
    first = provider.prefetch("what is my locker code")
    assert "MEMORY GAP" in first and "disk is full" in first
    assert "MEMORY GAP" not in provider.prefetch("what is my locker code")


def test_the_system_prompt_names_the_mind(provider, tmp_path):
    assert str(tmp_path / "tapestry" / "mind.db") in provider.system_prompt_block()


def test_failed_recall_is_loud_not_empty(provider, monkeypatch):
    provider._reader.remember("Sam likes tea.", source="user")
    def boom(*a, **k):
        raise hermes.RecallFailed("disk I/O error")
    monkeypatch.setattr(provider._reader, "recall", boom)
    assert provider.prefetch("what does Sam like to drink").startswith("[MEMORY UNAVAILABLE")


def test_fence_sequences_are_neutralized_on_the_way_out(provider):
    text = "The sanitizer strips <memory-context> tags and [System note: lines."
    provider._reader.remember(text, source="user")
    out = provider.prefetch("the sanitizer strips memory context tags and system note lines")
    assert "<memory-context>" not in out and "[System note:" not in out
    assert "<`memory-context>" in out and "[System-note:" in out
    # Stored content is untouched: escaping is the adapter's job, not the core's.
    stored = provider._reader.db.execute("SELECT content FROM memories").fetchone()[0]
    assert stored == text


def test_note_is_recallable_immediately(provider):
    saved = json.loads(provider.handle_tool_call("tapestry_note", {"source": "user", "content": "Sam's dog is Biscuit."}))
    assert "saved" in saved
    got = json.loads(provider.handle_tool_call("tapestry_recall", {"query": "dog Biscuit"}))
    assert "Biscuit" in got["memories"]


def test_why_shows_evidence(provider):
    mid = json.loads(provider.handle_tool_call("tapestry_note",
                                               {"source": "user", "content": "Sam banks with Northwind."}))["saved"]
    why = json.loads(provider.handle_tool_call("tapestry_why", {"memory": mid}))
    assert why["evidence"][0]["kind"] == "observe" and why["evidence"][0]["source"] == "user"


def test_note_can_supersede_a_recalled_memory(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    old = call("tapestry_note", source="user", content="Sam's dog is called Biscuit.")["saved"]
    new = call("tapestry_note", source="user", content="Sam's dog is called Waffles; Biscuit was wrong.",
               supersedes=old)["saved"]
    got = call("tapestry_recall", query="what is Sam's dog called")["memories"]
    assert f"#{new}" in got and f"#{old}" not in got
    why = call("tapestry_why", memory=old)["evidence"]
    assert why[-1]["kind"] == "supersede" and why[-1]["note"] == f"superseded by {new}"


def test_superseding_an_unknown_memory_saves_nothing(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    out = call("tapestry_note", source="user", content="Sam's cat is Pixel.", supersedes=99999)
    assert "error" in out and "99999" in out["error"]
    assert "Pixel" not in call("tapestry_recall", query="Sam's cat Pixel")["memories"]


@pytest.mark.parametrize("bad", [True, 1.7, "abc", -1.0, "\u0661"])
def test_supersedes_must_be_a_whole_number(provider, bad):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    first = call("tapestry_note", source="user", content="Sam's bike is a Trek.")["saved"]
    assert first == 1
    out = call("tapestry_note", source="user", content="Sam's bike is a Giant.", supersedes=bad)
    assert "error" in out and "#number" in out["error"]
    assert "#1" in call("tapestry_recall", query="Sam's bike")["memories"]


def test_superseding_an_already_superseded_memory_is_refused(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    a = call("tapestry_note", source="user", content="Sam's car is red.")["saved"]
    b = call("tapestry_note", source="user", content="Sam's car is blue.", supersedes=a)["saved"]
    out = call("tapestry_note", source="user", content="Sam's car is green.", supersedes=a)
    assert "error" in out and f"#{b}" in out["error"]
    assert "green" not in call("tapestry_recall", query="Sam's car colour")["memories"]
    assert [e["kind"] for e in call("tapestry_why", memory=a)["evidence"]].count("supersede") == 1


def test_cannot_supersede_a_memory_this_session_cannot_see(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_topics", action="describe", topic="finance", description="Money.")
    hidden = call("tapestry_note", source="user", content="Rent is $1,900.", topics=["finance"])["saved"]
    out = call("tapestry_note", source="user", content="Rent is $2,000.", supersedes=hidden)
    assert "error" in out and f"#{hidden}" in out["error"]
    assert "$2,000" not in call("tapestry_recall", query="rent", topics=["finance"])["memories"]
    # Filing the correction where the old one lives makes it visible, so it's allowed.
    ok = call("tapestry_note", source="user", content="Rent is $2,000.", supersedes=hidden, topics=["finance"])
    assert ok.get("supersedes") == hidden


def test_hidden_and_unknown_ids_get_the_same_refusal(provider):
    # Otherwise the refusal is an existence oracle for topics the session can't see.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_topics", action="describe", topic="finance", description="Money.")
    hidden = call("tapestry_note", source="user", content="Rent is $1,900.", topics=["finance"])["saved"]
    unknown = hidden + 500
    h = call("tapestry_note", source="user", content="x", supersedes=hidden)["error"]
    u = call("tapestry_note", source="user", content="x", supersedes=unknown)["error"]
    assert h.replace(f"#{hidden}", "#N") == u.replace(f"#{unknown}", "#N")
    # ...including a hidden memory that's already superseded (no "superseded by #M" leak)
    call("tapestry_note", source="user", content="Rent is $2,000.", supersedes=hidden, topics=["finance"])
    h2 = call("tapestry_note", source="user", content="x", supersedes=hidden)["error"]
    assert h2.replace(f"#{hidden}", "#N") == u.replace(f"#{unknown}", "#N")


@pytest.mark.parametrize("ref", ["#1", " #1 ", "1"])
def test_supersedes_accepts_the_hash_form_the_description_uses(provider, ref):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    assert call("tapestry_note", source="user", content="Sam's boat is a sloop.")["saved"] == 1
    out = call("tapestry_note", source="user", content="Sam's boat is a ketch.", supersedes=ref)
    assert out.get("supersedes") == 1, out


def test_topics_open_describe_and_search(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    assert "error" in call("tapestry_topics", action="open", topic="finance")
    call("tapestry_topics", action="describe", topic="finance", description="Money matters.")
    call("tapestry_note", source="user", content="Groceries run $600 a month.", topics=["finance"])
    assert call("tapestry_recall", query="groceries budget")["memories"] == ""
    got = call("tapestry_recall", query="groceries budget", topics=["finance"])
    assert "$600" in got["memories"] and got["searched"] == ["general", "finance"]
    opened = call("tapestry_topics", action="open", topic="finance")
    assert opened["open"] == ["finance"]
    assert "$600" in provider.prefetch("groceries run how much a month")
    assert "finance" in provider.system_prompt_block()


def test_recall_rejects_unknown_topics(provider):
    out = json.loads(provider.handle_tool_call("tapestry_recall", {"query": "x", "topics": ["nope"]}))
    assert "no topic nope" in out["error"]


def test_host_memory_writes_become_low_weight_evidence(provider):
    provider.on_memory_write("add", "user", "Sam prefers short answers.")
    drain(provider)
    ev = provider._reader.db.execute("SELECT source FROM evidence").fetchall()
    assert ev == [("host_memory",)]


def test_subagents_do_not_write(tmp_path):
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize("s1", hermes_home=str(tmp_path), platform="cli", agent_context="subagent")
    p.sync_turn("hello there", "hi")
    assert p._store.writes.empty()
    assert p._reader.db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
    p.shutdown()


def _session(tmp_path, sid):
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize(sid, hermes_home=str(tmp_path), platform="cli")
    return p


def test_a_hermes_episode_has_no_place_and_each_session_keeps_its_own(tmp_path):
    # A Hermes session has no place (docs/interfaces/HOOKS.md).
    a, b = _session(tmp_path, "chat-with-will"), _session(tmp_path, "pair-with-ash")
    try:
        a.sync_turn("My bike is a Trek.", "Noted.", session_id="chat-with-will")
        b.sync_turn("Ash, check the logs.", "On it.", session_id="pair-with-ash")
        drain(a)
        assert a._reader.episode("chat-with-will").place is None
        assert _turns(a, "chat-with-will") == [("user", "My bike is a Trek."), ("agent", "Noted.")]
        assert _turns(a, "pair-with-ash") == [("user", "Ash, check the logs."), ("agent", "On it.")]
    finally:
        a.shutdown()
        b.shutdown()


def test_notes_are_shared_across_sessions(tmp_path):
    """The deliberate path reaches everyone: with no place, a note lives in the general topic."""
    a = _session(tmp_path, "chat-with-will")
    b = _session(tmp_path, "pair-with-ash")
    try:
        json.loads(a.handle_tool_call("tapestry_note", {"source": "user", "content": "Sam's dog is Biscuit."}))
        assert "Biscuit" in b.prefetch("what is Sam's dog called")
    finally:
        a.shutdown()
        b.shutdown()


def test_a_session_starts_knowing_where_things_stand(tmp_path):
    # docs/capabilities/RECALL.md: a session never starts as a stranger by default.
    p = _session(tmp_path, "s-one")
    try:
        _note(p, "Sam is midway through moving the house automations to Home Assistant; what is unfinished is the garage door.")
        assert "garage door" in p.system_prompt_block()
    finally:
        p.shutdown()


def test_a_session_end_ends_the_episode_and_starts_background_work(tmp_path, monkeypatch):
    # docs/interfaces/HOOKS.md: a behaviour that differs by host is a bug; Claude Code's
    # session end starts jobs, so Hermes' does too.
    from tapestry import jobs
    started = []
    monkeypatch.setattr(jobs, "start", lambda log, *extra: started.append(extra))
    p = _session(tmp_path, "s-end")
    try:
        p.sync_turn("We moved the gateway to the Pi.", "Noted.", session_id="s-end")
        p.on_session_end([])
        assert p._reader.db.execute("SELECT ended_at FROM episodes WHERE id='s-end'").fetchone()[0]
        assert started and started[0][:2] == ("--mind", str(tmp_path / "tapestry" / "mind.db"))
    finally:
        p.shutdown()


def _writer_threads():
    import threading
    return sum(1 for t in threading.enumerate() if t.name == "tapestry-writer" and t.is_alive())


def _open_fds():
    import os
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("counts open files through /proc, which this platform lacks")
    return len(os.listdir("/proc/self/fd"))


def test_dropped_providers_do_not_accumulate(tmp_path):
    """Hermes can drop a provider without shutdown() (agent-cache eviction, then a
    resumed session builds a new agent). However many it creates and forgets,
    one mind costs one writer thread and a fixed set of connections."""
    import gc
    first = hermes.TapestryProvider(encoder_factory=toy_factory)
    first.initialize("s0", hermes_home=str(tmp_path), platform="cli")
    # Settle the baseline: the writer opens its connection asynchronously, and
    # WAL side files appear on first read and first write.
    first.sync_turn("warm up the writer", "ok", session_id="s0")
    drain(first)
    first.prefetch("anything at all here")
    threads, fds = _writer_threads(), _open_fds()
    for i in range(1, 8):
        p = hermes.TapestryProvider(encoder_factory=toy_factory)
        p.initialize(f"s{i}", hermes_home=str(tmp_path), platform="cli")
        p.prefetch("anything at all here")
        del p
    gc.collect()
    assert _writer_threads() == threads
    assert _open_fds() == fds
    first.shutdown()


def test_writes_from_a_dropped_provider_still_land(tmp_path):
    import gc
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize("s-drop", hermes_home=str(tmp_path), platform="cli")
    p.sync_turn("Remember the gate code is 4471.", "Got it.", session_id="s-drop")
    store = p._store
    del p
    gc.collect()
    assert store.flush(timeout=10)
    texts = [r[0] for r in store.reader.db.execute("SELECT text FROM turns")]
    assert "Remember the gate code is 4471." in texts and "Got it." in texts


def test_shutdown_makes_writes_durable_and_leaves_siblings_working(tmp_path):
    a = hermes.TapestryProvider(encoder_factory=toy_factory)
    a.initialize("s-a", hermes_home=str(tmp_path), platform="cli")
    b = hermes.TapestryProvider(encoder_factory=toy_factory)
    b.initialize("s-b", hermes_home=str(tmp_path), platform="cli")
    a.handle_tool_call("tapestry_note", {"source": "user", "content": "Sam's dog is Biscuit."})
    a.sync_turn("My favorite color is teal.", "Noted.", session_id="s-a")
    a.shutdown()
    # a's queued turn is on disk the moment shutdown returns.
    assert ("user", "My favorite color is teal.") in _turns(b, "s-a")
    # b shares the store and is unaffected by a's shutdown.
    assert "Biscuit" in b.prefetch("what is Sam's dog called")
    b.sync_turn("The gate code is 4471.", "Got it.", session_id="s-b")
    drain(b)
    assert ("user", "The gate code is 4471.") in _turns(b, "s-b")
    b.shutdown()


def test_default_encoder_is_built_once_per_process(monkeypatch):
    from tapestry import embed
    built = []

    class Counting:
        def __init__(self):
            built.append(self)

    monkeypatch.setattr(embed, "Encoder", Counting)
    monkeypatch.setattr(hermes, "_ENCODER", None)
    first = hermes._default_encoder()
    second = hermes._default_encoder()
    assert first[0] is second[0] and len(built) == 1


def test_tool_schemas_use_parameters():
    for t in hermes.TOOLS:
        assert "parameters" in t and "input_schema" not in t


def test_why_reveals_nothing_about_a_memory_this_session_cannot_see(provider):
    # A topic that isn't searched leaves no trace (docs/primitives/TOPIC.md):
    # why answers a hidden memory exactly as it answers one that doesn't exist.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_topics", action="describe", topic="finance", description="Money.")
    hidden = call("tapestry_note", source="user", content="Rent is $1,900.", topics=["finance"])["saved"]
    unknown = hidden + 500
    h = call("tapestry_why", memory=hidden)
    u = call("tapestry_why", memory=unknown)
    assert set(h) == {"error"} and h["error"].replace(f"#{hidden}", "#N") == u["error"].replace(f"#{unknown}", "#N")
    call("tapestry_topics", action="open", topic="finance")
    assert call("tapestry_why", memory=hidden)["evidence"][0]["kind"] == "observe"


def test_why_answers_for_what_recall_just_returned(provider):
    # "Why do you believe this?" always has an answer (docs/primitives/EVIDENCE.md): a memory
    # recall returned by naming a topic can be explained by naming it again, without
    # opening it for the session. Naming a topic is searching it, so the hidden-equals-
    # missing rule above still holds when it isn't named (docs/primitives/TOPIC.md).
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_topics", action="describe", topic="finance", description="Money.")
    mid = call("tapestry_note", source="user", content="Rent is $1,900.", topics=["finance"])["saved"]
    assert "1,900" in call("tapestry_recall", query="rent", topics=["finance"])["memories"]
    why = call("tapestry_why", memory=mid, topics=["finance"])
    assert any(e["kind"] == "file" and e["note"] == "finance" for e in why["evidence"])
    assert "error" in call("tapestry_why", memory=mid)          # not opened by the lookup
    assert "error" in call("tapestry_why", memory=mid, topics=["nope"])


@pytest.mark.parametrize("ref,ok", [(1, True), ("1", True), ("#1", True), (" #1 ", True),
                                    (True, False), (1.0, False), ("#x", False),
                                    ("\u00b2", False), ("\u0661", False)])
def test_why_takes_the_number_form_the_descriptions_use(provider, ref, ok):
    # The tools describe ids as "#number"; why and note's supersedes parse them alike, and a
    # bool or float is refused rather than read as a memory (int(True) is 1).
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    assert call("tapestry_note", source="user", content="Sam likes tea.")["saved"] == 1
    out = call("tapestry_why", memory=ref)
    assert ("evidence" in out) is ok, out
    if not ok:
        assert "#number" in out["error"], out


def test_every_note_says_who_it_came_from(provider):
    # Every evidence call carries its source (docs/interfaces/TOOLS.md); a note
    # without one is refused rather than attributed to the user by default.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    out = call("tapestry_note", content="Sam prefers window seats.")
    assert "error" in out and "Nothing saved" in out["error"]
    assert "window" not in call("tapestry_recall", query="Sam window seats")["memories"]
    mid = call("tapestry_note", source="agent", content="Sam probably flies often.")["saved"]
    assert call("tapestry_why", memory=mid)["evidence"][0]["source"] == "agent"


def test_a_supersede_is_recorded_as_the_agents_judgment(provider):
    # Whether a new memory replaces an old one is the agent's call
    # (docs/primitives/MEMORY.md), even when the new memory is the user's words.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    old = call("tapestry_note", source="user", content="Sam's favorite color is pink.")["saved"]
    new = call("tapestry_note", source="user", content="Sam's favorite color is blue now.",
               supersedes=old)["saved"]
    assert call("tapestry_why", memory=new)["evidence"][0]["source"] == "user"
    sup = [e for e in call("tapestry_why", memory=old)["evidence"] if e["kind"] == "supersede"]
    assert [e["source"] for e in sup] == ["agent"]


@pytest.mark.parametrize("limit, most", [(1000, 20), (-1, 1), (0, 5)])
def test_recall_output_fits_the_budget(provider, limit, most):
    # Tool output stays within the agent's budget (docs/interfaces/TOOLS.md).
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    for i in range(30):
        call("tapestry_note", source="user", content=f"Sam owns plant number {i}.")
    got = call("tapestry_recall", query="Sam plant", limit=limit)["memories"]
    assert 1 <= got.count("#") <= most


def test_tool_recall_stays_within_the_budget_and_never_points_at_itself(provider):
    # docs/interfaces/TOOLS.md: tool output stays within the agent's budget.
    from tapestry import render
    for i in range(5):
        _note(provider, f"Sam's garden plan, part {i}: " + "sam's garden plan " * 190)
    out = json.loads(provider.handle_tool_call("tapestry_recall", {"query": "Sam's garden plan"}))["memories"]
    assert len(out) < render.PROMPT_BUDGET + 200
    assert "didn't fit; a narrower query shows them" in out and "tapestry_recall shows" not in out


def test_why_says_how_much_a_memory_is_believed_and_because_of_what(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    mid = call("tapestry_note", source="user", content="Sam's name is Samuel.", volatility="immutable")["saved"]
    why = call("tapestry_why", memory=mid)
    assert why["belief"]["label"].startswith("very likely") and "the user said it" in why["belief"]["because"]


def test_the_users_answer_moves_belief_and_the_agents_own_use_does_not(provider):
    # docs/interfaces/TOOLS.md: record_answer records the user's reply; use is never evidence.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    mid = call("tapestry_note", source="web", content="Sam lives in Austin.", volatility="stable")["saved"]
    before = call("tapestry_why", memory=mid)["belief"]["label"]
    for _ in range(3):
        call("tapestry_recall", query="where does Sam live")
        call("tapestry_why", memory=mid)
    assert call("tapestry_why", memory=mid)["belief"]["label"] == before
    done = call("tapestry_record_answer", memory=mid, answer="confirmed")
    assert done["recorded"] == "confirmed" and done["belief"]["label"] != before
    assert "contradicted since" in call("tapestry_record_answer", memory=mid,
                                        answer="contradicted")["belief"]["because"]


def test_an_answer_cannot_be_recorded_for_what_this_session_cannot_see_or_for_a_bad_answer(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    hidden = call("tapestry_note", source="user", content="Sam's salary is $105,000.",
                  topics=["finance"])["saved"]
    assert "error" in call("tapestry_record_answer", memory=hidden, answer="confirmed")
    shown = call("tapestry_note", source="user", content="Sam likes tea.")["saved"]
    assert "error" in call("tapestry_record_answer", memory=shown, answer="maybe")
    assert call("tapestry_why", memory=shown)["evidence"][-1]["kind"] == "observe"


def test_forget_needs_the_users_confirmation_and_is_then_complete(provider):
    # docs/interfaces/TOOLS.md: forget, after the user confirms. docs/primitives/MEMORY.md: completely.
    call = lambda **a: json.loads(provider.handle_tool_call("tapestry_forget", a))
    mid = _note(provider, "Sam's locker code is 4417.")
    asked = call(memory=mid)
    assert asked["forgotten"] is False and "4417" in asked["says"]
    assert "4417" in json.loads(provider.handle_tool_call("tapestry_recall", {"query": "locker code"}))["memories"]
    assert call(memory=mid, confirmed="yes")["forgotten"] is False      # only a real true counts
    assert call(memory=mid, confirmed=True)["forgotten"] is True
    assert json.loads(provider.handle_tool_call("tapestry_recall", {"query": "locker code"}))["memories"] == ""
    why = json.loads(provider.handle_tool_call("tapestry_why", {"memory": mid}))
    assert "forgotten" in why["error"] and "4417" not in json.dumps(why)


def test_forget_cannot_reach_a_memory_this_session_cannot_see(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    hidden = call("tapestry_note", source="user", content="Sam's salary is $105,000.",
                  topics=["finance"])["saved"]
    refused = call("tapestry_forget", memory=hidden, confirmed=True)
    assert "error" in refused and "105,000" not in json.dumps(refused)
    assert call("tapestry_recall", query="salary", topics=["finance"])["memories"]


SPILL_CAP = 10_000   # Hermes' hooks.output_spill.max_chars default; over it, recall lands as ~1k


def _note(p, text):
    return json.loads(p.handle_tool_call("tapestry_note", {"source": "user", "content": text}))["saved"]


def test_prefetch_stays_under_the_spill_cap_with_whole_memories_in_rank_order(provider):
    for i in range(5):
        _note(provider, f"Sam's garden plan, part {i}: " + "sam's garden plan " * 190)
    from tapestry import render
    ranked = render.block(provider._reader.recall(
        "Sam's garden plan", topics=provider._open, k=3))
    assert len(ranked) > SPILL_CAP          # the case that used to spill
    out = provider.prefetch("Sam's garden plan")
    assert len(out) < SPILL_CAP
    ids = [l.split(" ·")[0] for l in out.splitlines() if l.startswith("[#")]
    order = [l.split(" ·")[0] for l in ranked.splitlines() if l.startswith("[#")]
    assert len(ids) >= 2 and ids == order[:len(ids)]
    for l in out.splitlines():              # every memory shown is shown whole
        if l.startswith("[#"):
            assert l in ranked.splitlines()
    assert "didn't fit; tapestry_recall shows them" in out


def test_one_memory_over_the_budget_is_cut_and_says_so(provider):
    _note(provider, "Sam's novel draft: " + "sam's novel draft " * 700)
    out = provider.prefetch("Sam's novel draft")
    assert len(out) < SPILL_CAP and out.startswith("[#1 ")
    assert "[cut: " in out and "tapestry_recall shows it whole" in out


def test_a_budget_drops_the_weakest_matches_first_never_a_stronger_one_for_a_weaker():
    # docs/capabilities/RECALL.md: when recall leaves things out, the weakest go first.
    from tapestry import render
    from tapestry.mind import Recalled
    r = lambda i, n: Recalled(i, "x" * n, (), 1.0 / i, 0.9, "semantic", 0.0)
    out = render.budgeted([r(1, 100), r(2, 5000), r(3, 100)], budget=600)
    shown = [l.split(" ·")[0] for l in out.splitlines() if l.startswith("[#")]
    assert shown == ["[#1"] and "(2 more memories matched" in out
