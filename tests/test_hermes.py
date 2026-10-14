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
    assert p._store.flush(timeout=10)


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
    call("tapestry_namespaces", action="describe", namespace="finance", description="Money.")
    hidden = call("tapestry_note", source="user", content="Rent is $1,900.", namespaces=["finance"])["saved"]
    out = call("tapestry_note", source="user", content="Rent is $2,000.", supersedes=hidden)
    assert "error" in out and f"#{hidden}" in out["error"]
    assert "$2,000" not in call("tapestry_recall", query="rent", namespaces=["finance"])["memories"]
    # Filing the correction where the old one lives makes it visible, so it's allowed.
    ok = call("tapestry_note", source="user", content="Rent is $2,000.", supersedes=hidden, namespaces=["finance"])
    assert ok.get("supersedes") == hidden


def test_hidden_and_unknown_ids_get_the_same_refusal(provider):
    # Otherwise the refusal is an existence oracle for namespaces the session can't see.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_namespaces", action="describe", namespace="finance", description="Money.")
    hidden = call("tapestry_note", source="user", content="Rent is $1,900.", namespaces=["finance"])["saved"]
    unknown = hidden + 500
    h = call("tapestry_note", source="user", content="x", supersedes=hidden)["error"]
    u = call("tapestry_note", source="user", content="x", supersedes=unknown)["error"]
    assert h.replace(f"#{hidden}", "#N") == u.replace(f"#{unknown}", "#N")
    # ...including a hidden memory that's already superseded (no "superseded by #M" leak)
    call("tapestry_note", source="user", content="Rent is $2,000.", supersedes=hidden, namespaces=["finance"])
    h2 = call("tapestry_note", source="user", content="x", supersedes=hidden)["error"]
    assert h2.replace(f"#{hidden}", "#N") == u.replace(f"#{unknown}", "#N")


@pytest.mark.parametrize("ref", ["#1", " #1 ", "1"])
def test_supersedes_accepts_the_hash_form_the_description_uses(provider, ref):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    assert call("tapestry_note", source="user", content="Sam's boat is a sloop.")["saved"] == 1
    out = call("tapestry_note", source="user", content="Sam's boat is a ketch.", supersedes=ref)
    assert out.get("supersedes") == 1, out


def test_namespaces_open_describe_and_search(provider):
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    assert "error" in call("tapestry_namespaces", action="open", namespace="finance")
    call("tapestry_namespaces", action="describe", namespace="finance", description="Money matters.")
    call("tapestry_note", source="user", content="Groceries run $600 a month.", namespaces=["finance"])
    assert call("tapestry_recall", query="groceries budget")["memories"] == ""
    got = call("tapestry_recall", query="groceries budget", namespaces=["finance"])
    assert "$600" in got["memories"] and got["searched"] == ["default", "session.s1", "finance"]
    opened = call("tapestry_namespaces", action="open", namespace="finance")
    assert opened["open"] == ["finance"]
    assert "$600" in provider.prefetch("what is the groceries budget")
    assert "finance" in provider.system_prompt_block()


def test_recall_rejects_unknown_namespaces(provider):
    out = json.loads(provider.handle_tool_call("tapestry_recall", {"query": "x", "namespaces": ["nope"]}))
    assert "no namespace nope" in out["error"]


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
    assert p._reader.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
    p.shutdown()


def _session(tmp_path, sid):
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize(sid, hermes_home=str(tmp_path), platform="cli")
    return p


def test_hermes_files_turns_in_its_session_only(tmp_path):
    # A Hermes session has no place (docs/interfaces/HOOKS.md), so by the core's one
    # rule (ADR-0019) its turns go to its own session namespace, never the default.
    p = _session(tmp_path, "chat-with-will")
    try:
        p.sync_turn("My bike is a Trek.", "Noted.", session_id="chat-with-will")
        drain(p)
        rows = p._reader.db.execute(
            "SELECT m.content, group_concat(n.name) FROM memories m LEFT JOIN memory_namespaces x "
            "ON x.memory_id = m.id LEFT JOIN namespaces n ON n.id = x.namespace_id GROUP BY m.id").fetchall()
        assert sorted(rows) == [("My bike is a Trek.", "session.chat-with-will"),
                                ("Noted.", "session.chat-with-will")]
    finally:
        p.shutdown()


def test_turns_stay_in_their_own_session(tmp_path):
    """A turn is visible to the session it happened in, not to every other one."""
    a = _session(tmp_path, "chat-with-will")
    a.sync_turn("My favorite color is teal.", "Noted, teal it is.", session_id="chat-with-will")
    drain(a)
    b = _session(tmp_path, "pair-with-ash")
    try:
        assert "teal" in a.prefetch("what is my favorite color")
        assert b.prefetch("what is my favorite color") == ""
        # Reachable on purpose: naming the namespace searches it.
        ns = hermes.session_namespace("chat-with-will")
        got = json.loads(b.handle_tool_call("tapestry_recall",
                                            {"query": "favorite color", "namespaces": [ns]}))
        assert "teal" in got["memories"]
    finally:
        a.shutdown()
        b.shutdown()


def test_notes_are_shared_across_sessions(tmp_path):
    """The deliberate path still reaches everyone: a note defaults to the shared namespace."""
    a = _session(tmp_path, "chat-with-will")
    b = _session(tmp_path, "pair-with-ash")
    try:
        json.loads(a.handle_tool_call("tapestry_note", {"source": "user", "content": "Sam's dog is Biscuit."}))
        assert "Biscuit" in b.prefetch("what is Sam's dog called")
    finally:
        a.shutdown()
        b.shutdown()


def test_directory_counts_other_sessions_without_listing_them(tmp_path):
    for sid in ("s-one", "s-two", "s-three"):
        p = _session(tmp_path, sid)
        p.sync_turn(f"hello from {sid}", "hi", session_id=sid)
        drain(p)
        p.shutdown()
    p = _session(tmp_path, "s-one")
    try:
        block = p.system_prompt_block()
        assert "session.s-one" in block
        assert "session.s-two" not in block and "session.s-three" not in block
        assert "plus 2 other conversations" in block
    finally:
        p.shutdown()


def test_session_namespace_is_a_valid_name():
    for sid in ("20260831_210533_01629a01", "api_1789786014_c0a07a8f", "Weird ID/with spaces", ""):
        name = hermes.session_namespace(sid)
        assert name.startswith("session.")
        assert hermes.re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name)


def _writer_threads():
    import threading
    return sum(1 for t in threading.enumerate() if t.name == "tapestry-writer" and t.is_alive())


def _open_fds():
    import os
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
    contents = [r[0] for r in store.reader.db.execute("SELECT content FROM memories")]
    assert "Remember the gate code is 4471." in contents and "Got it." in contents


def test_shutdown_makes_writes_durable_and_leaves_siblings_working(tmp_path):
    a = hermes.TapestryProvider(encoder_factory=toy_factory)
    a.initialize("s-a", hermes_home=str(tmp_path), platform="cli")
    b = hermes.TapestryProvider(encoder_factory=toy_factory)
    b.initialize("s-b", hermes_home=str(tmp_path), platform="cli")
    a.handle_tool_call("tapestry_note", {"source": "user", "content": "Sam's dog is Biscuit."})
    a.sync_turn("My favorite color is teal.", "Noted.", session_id="s-a")
    a.shutdown()
    # a's queued turn is on disk the moment shutdown returns.
    rows = [r[0] for r in b._reader.db.execute("SELECT content FROM memories")]
    assert "My favorite color is teal." in rows
    # b shares the store and is unaffected by a's shutdown.
    assert "Biscuit" in b.prefetch("what is Sam's dog called")
    b.sync_turn("The gate code is 4471.", "Got it.", session_id="s-b")
    drain(b)
    assert "4471" in b.prefetch("what is the gate code")
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
    # A namespace that isn't searched leaves no trace (docs/primitives/NAMESPACE.md):
    # why answers a hidden memory exactly as it answers one that doesn't exist.
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_namespaces", action="describe", namespace="finance", description="Money.")
    hidden = call("tapestry_note", source="user", content="Rent is $1,900.", namespaces=["finance"])["saved"]
    unknown = hidden + 500
    h = call("tapestry_why", memory=hidden)
    u = call("tapestry_why", memory=unknown)
    assert set(h) == {"error"} and h["error"].replace(f"#{hidden}", "#N") == u["error"].replace(f"#{unknown}", "#N")
    call("tapestry_namespaces", action="open", namespace="finance")
    assert call("tapestry_why", memory=hidden)["evidence"][0]["kind"] == "observe"


def test_why_answers_for_what_recall_just_returned(provider):
    # "Why do you believe this?" always has an answer (docs/primitives/EVIDENCE.md): a memory
    # recall returned by naming a namespace can be explained by naming it again, without
    # opening it for the session. Naming a namespace is searching it, so the hidden-equals-
    # missing rule above still holds when it isn't named (docs/primitives/NAMESPACE.md).
    call = lambda name, **a: json.loads(provider.handle_tool_call(name, a))
    call("tapestry_namespaces", action="describe", namespace="finance", description="Money.")
    mid = call("tapestry_note", source="user", content="Rent is $1,900.", namespaces=["finance"])["saved"]
    assert "1,900" in call("tapestry_recall", query="rent", namespaces=["finance"])["memories"]
    why = call("tapestry_why", memory=mid, namespaces=["finance"])
    assert any(e["kind"] == "file" and e["note"] == "finance" for e in why["evidence"])
    assert "error" in call("tapestry_why", memory=mid)          # not opened by the lookup
    assert "error" in call("tapestry_why", memory=mid, namespaces=["nope"])


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


def test_tools_search_the_sessions_own_namespace(provider):
    # A session searches its own namespace automatically (docs/primitives/NAMESPACE.md),
    # through its tools as well as through recall before each turn.
    provider.sync_turn("My locker code is 4417.", "Got it.", session_id="s1")
    drain(provider)
    got = json.loads(provider.handle_tool_call("tapestry_recall", {"query": "locker code"}))
    assert "4417" in got["memories"]
