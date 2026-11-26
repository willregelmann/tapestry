"""The server and a machine connected to it (PLAN.md stage 6; docs/primitives/KEY.md, HOOKS.md)."""

from __future__ import annotations

import json
import threading

import pytest

from tapestry import mcp_server, remote, server
from tapestry.hosts import claude_code as cc
from tapestry.mind import Mind
from tests.test_mind import toy_encode


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPESTRY_SERVER", raising=False)
    monkeypatch.delenv("TAPESTRY_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_JUDGE", "")
    home = tmp_path / "server"
    reg = server.Registry(home)
    reg.add_mind("will")
    reg.add_mind("ash")
    app = server.Server(home, encode=toy_encode, model_tag="toy", background=False)
    httpd = server.http_server(app, "127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield {"url": url, "reg": reg, "app": app, "home": home}
    httpd.shutdown()
    app.close()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "atlas"
    (r / ".git").mkdir(parents=True)
    return r


def machine(tmp_path, monkeypatch, srv, name, *, mind="will", agent="claude-code", read_only=False):
    """A machine's own home, connected through its own key. Returns a function that makes it current."""
    key, token = srv["reg"].add_key(mind=mind, agent=agent, machine=name, read_only=read_only)
    home = tmp_path / f"machine-{name}"
    remote.connect(home, srv["url"], token)
    def use():
        monkeypatch.setenv("TAPESTRY_HOME", str(home))
    use.key, use.token = key, token
    return use


def hook(event, **kw):
    out = cc.run_hook(json.dumps({"hook_event_name": event, **kw}))
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


def tool(cwd, session, name, **args):
    s = mcp_server.Server(cwd=str(cwd), instance="none")
    s._sid = None
    import os
    os.environ["CLAUDE_CODE_SESSION_ID"] = session
    try:
        return json.loads(s._tools().call(name, args))
    finally:
        del os.environ["CLAUDE_CODE_SESSION_ID"]


def transcript(tmp_path, *turns):
    f = tmp_path / "t.jsonl"
    f.write_text("".join(json.dumps({"type": who, "uuid": f"u{i}", "timestamp": "2026-11-23T10:00:00Z",
                                     "message": {"content": text}}) + "\n"
                         for i, (who, text) in enumerate(turns)))
    return f


def test_a_machine_recalls_what_another_machine_noted_in_the_same_mind(tmp_path, monkeypatch, srv, repo):
    """The MVP's promise: tell it on one machine, and the other already knows."""
    laptop, desktop = machine(tmp_path, monkeypatch, srv, "laptop"), machine(tmp_path, monkeypatch, srv, "desktop")
    laptop()
    hook("SessionStart", session_id="s1", cwd=str(repo))
    assert "saved" in tool(repo, "s1", "tapestry_note", content="Atlas deploys to Fly.io.", source="user")
    desktop()
    start = hook("SessionStart", session_id="s2", cwd=str(repo))
    assert "Atlas deploys to Fly.io." in start
    assert "Atlas deploys to Fly.io." in hook("UserPromptSubmit", session_id="s2", cwd=str(repo),
                                              prompt="Atlas deploys to Fly.io?")


def test_every_turn_and_entry_names_the_key_it_came_through(tmp_path, monkeypatch, srv, repo):
    laptop, desktop = machine(tmp_path, monkeypatch, srv, "laptop"), machine(tmp_path, monkeypatch, srv, "desktop")
    laptop()
    cc.ingest(str(transcript(tmp_path, ("user", "We deploy atlas to Fly.io now."))), str(repo), "s1")
    desktop()
    tool(repo, "s2", "tapestry_note", content="Atlas has 40 tests.", source="user")
    m = srv["app"].served("will").mind
    assert m.db.execute("SELECT via FROM turns").fetchall() == [(laptop.key["id"],)]
    assert {r[0] for r in m.db.execute("SELECT via FROM evidence WHERE kind='observe'")} == {desktop.key["id"]}


def test_a_key_reaches_exactly_one_mind(tmp_path, monkeypatch, srv, repo):
    will, ash = machine(tmp_path, monkeypatch, srv, "laptop"), machine(tmp_path, monkeypatch, srv, "pi", mind="ash", agent="ash")
    will()
    tool(repo, "s1", "tapestry_note", content="Will's locker code is 4417.", source="user", topics=[])
    ash()
    found = tool(repo, "s2", "tapestry_recall", query="locker code")
    assert "4417" not in json.dumps(found)
    assert srv["app"].served("ash").mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_a_revoked_key_is_refused_on_its_next_request_and_says_so(tmp_path, monkeypatch, srv, repo):
    laptop = machine(tmp_path, monkeypatch, srv, "laptop")
    laptop()
    assert "MEMORY UNAVAILABLE" not in hook("SessionStart", session_id="s1", cwd=str(repo))
    srv["reg"].revoke(laptop.key["id"])
    out = hook("UserPromptSubmit", session_id="s1", cwd=str(repo), prompt="where does atlas deploy to?")
    assert out.startswith("[MEMORY UNAVAILABLE") and "401" in out
    assert "error" in tool(repo, "s1", "tapestry_recall", query="atlas")


def test_a_read_only_key_reads_but_records_nothing_and_says_so(tmp_path, monkeypatch, srv, repo):
    writer, reader = machine(tmp_path, monkeypatch, srv, "laptop"), machine(tmp_path, monkeypatch, srv, "kiosk", read_only=True)
    writer()
    tool(repo, "s1", "tapestry_note", content="Atlas deploys to Fly.io.", source="user", topics=[])
    reader()
    start = hook("SessionStart", session_id="s2", cwd=str(repo))
    assert "READS MEMORY ONLY" in start and "Atlas deploys to Fly.io." in start
    refused = tool(repo, "s2", "tapestry_note", content="Atlas has 40 tests.", source="user")
    assert "reads only" in refused["error"]
    with pytest.raises(remote.Unreachable, match="403"):
        cc.ingest(str(transcript(tmp_path, ("user", "Remember this."))), str(repo), "s2")
    m = srv["app"].served("will").mind
    assert m.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
    assert m.db.execute("SELECT count(*) FROM episodes WHERE id='s2'").fetchone()[0] == 0


def test_a_server_that_cannot_be_reached_is_memory_unavailable_never_empty(tmp_path, monkeypatch, repo):
    home = tmp_path / "machine"
    home.mkdir()
    (home / "remote.json").write_text(json.dumps({"url": "http://127.0.0.1:9", "token": "tap_x"}))
    monkeypatch.setenv("TAPESTRY_HOME", str(home))
    assert hook("SessionStart", session_id="s1", cwd=str(repo)).startswith("[MEMORY UNAVAILABLE")
    assert hook("UserPromptSubmit", session_id="s1", cwd=str(repo), prompt="where does atlas deploy?").startswith(
        "[MEMORY UNAVAILABLE")
    assert "couldn't be reached" in tool(repo, "s1", "tapestry_recall", query="atlas")["error"]


def test_recall_through_the_server_is_what_the_same_mind_gives_on_this_machine(tmp_path, monkeypatch, srv, repo):
    """docs/interfaces/HOOKS.md: a behaviour that differs by how the mind is reached is a bug."""
    laptop = machine(tmp_path, monkeypatch, srv, "laptop")
    laptop()
    for text in ("Atlas deploys to Fly.io.", "Atlas runs its tests with pytest.", "Sam keeps bees."):
        tool(repo, "s1", "tapestry_note", content=text, source="user")
    remote_out = hook("UserPromptSubmit", session_id="s9", cwd=str(repo), prompt="Atlas runs its tests with pytest?")
    local_home = tmp_path / "local"
    local_home.mkdir()
    monkeypatch.setenv("TAPESTRY_HOME", str(local_home))
    path = srv["reg"].minds()["will"]
    monkeypatch.setattr(cc, "open_mind", lambda cwd=None: Mind(path, encode=toy_encode, model_tag="toy"))
    local_out = hook("UserPromptSubmit", session_id="s9", cwd=str(repo), prompt="Atlas runs its tests with pytest?")
    assert remote_out == local_out and "pytest" in remote_out


def test_the_server_keeps_only_a_hash_of_each_token(tmp_path, srv):
    key, token = srv["reg"].add_key(mind="will", agent="claude-code", machine="laptop")
    assert token not in (srv["home"] / "keys.json").read_text()
    assert srv["reg"].key_for(token)["id"] == key["id"] and srv["reg"].key_for(token + "x") is None


def test_backups_older_than_the_limit_are_deleted_so_forgetting_reaches_them(tmp_path, srv):
    import os
    from tapestry import backup
    srv["app"].served("will").mind.remember("Atlas deploys to Fly.io.", source="user")
    to = tmp_path / "backups"
    made = backup.back_up(srv["home"], to, keep=14)
    assert [p.name.split("-")[0] for p in made] == ["will"]
    copy = Mind(made[0], encode=toy_encode, model_tag="toy")
    assert copy.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
    copy.close()
    old = time_now = __import__("time").time()
    os.utime(made[0], (old - 15 * 86400, old - 15 * 86400))
    backup.back_up(srv["home"], to, keep=14, now=time_now + 1)
    assert made[0].name not in {p.name for p in to.glob("*.db")}


# -- the viewer on the server (docs/interfaces/VIEWER.md) ------------------------------------

def view(srv, path, token, body=None):
    """One viewer request as the page makes it. Returns (status, reply)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(srv["url"] + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Tapestry-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def act(srv, token, name, mind="will", **args):
    code, out = view(srv, "/api/act", token, {"mind": mind, "name": name, "arguments": args})
    assert code == 200
    return out


def snapshot(m):
    """Everything in a mind that use could change."""
    return {t: m.db.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
            for (t,) in m.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                                     " AND name NOT LIKE '%fts%' AND name NOT LIKE 'vec%'")}


def test_only_an_owner_token_opens_the_viewer_and_it_opens_nothing_else(tmp_path, monkeypatch, srv, repo):
    """docs/interfaces/VIEWER.md: it shows only minds the owner owns, only to the owner."""
    laptop = machine(tmp_path, monkeypatch, srv, "laptop")
    owner, token = srv["reg"].add_owner()
    assert view(srv, "/api/status?mind=will", laptop.token)[0] == 401
    assert view(srv, "/api/status?mind=will", "tapo_wrong")[0] == 401
    code, status = view(srv, "/api/status?mind=ash", token)
    assert code == 200 and status["mind"] == "ash" and set(status["minds"]) == {"will", "ash"} and status["can_change"]
    with pytest.raises(remote.Unreachable, match="401"):
        remote.call({"url": srv["url"], "token": token}, "whoami", {})
    assert token not in (srv["home"] / "owner.json").read_text()
    srv["reg"].revoke(owner["id"])
    assert view(srv, "/api/status?mind=will", token)[0] == 401


def test_looking_through_the_viewer_on_the_server_is_never_use(tmp_path, monkeypatch, srv, repo):
    laptop = machine(tmp_path, monkeypatch, srv, "laptop")
    laptop()
    hook("SessionStart", session_id="s1", cwd=str(repo))
    a = tool(repo, "s1", "tapestry_note", content="Atlas deploys to Fly.io.", source="user")["saved"]
    tool(repo, "s1", "tapestry_note", content="Atlas runs its tests with pytest.", source="user", builds_on=[a])
    _, token = srv["reg"].add_owner()
    m = srv["app"].served("will").mind
    before = snapshot(m)
    for path in ("/api/status", "/api/memories?topics=atlas", f"/api/memory/{a}", "/api/changes",
                 "/api/recall?q=Atlas%20deploys%20to%20Fly.io%3F&topics=atlas"):
        code, out = view(srv, path + ("&" if "?" in path else "?") + "mind=will", token)
        assert code == 200 and "error" not in out, (path, out)
    assert snapshot(m) == before


def test_a_change_in_the_viewer_is_recorded_as_the_same_act_through_an_agent_but_from_the_owner(
        tmp_path, monkeypatch, srv, repo):
    laptop = machine(tmp_path, monkeypatch, srv, "laptop")
    laptop()
    by_agent = tool(repo, "s1", "tapestry_note", content="Atlas deploys to Fly.io.", source="user", topics=[])["saved"]
    owner, token = srv["reg"].add_owner()
    # What the owner writes is the owner's word, whatever the request claims.
    by_owner = act(srv, token, "tapestry_note", content="Atlas has 40 tests.", source="agent", topics=[])["saved"]
    m = srv["app"].served("will").mind
    ev = lambda mid: m.db.execute("SELECT kind, source, via FROM evidence WHERE memory_id=?", (mid,)).fetchall()
    assert ev(by_agent) == [("observe", "user", laptop.key["id"])]
    assert ev(by_owner) == [("observe", "user", owner["id"])]
    fixed = act(srv, token, "tapestry_note", content="Atlas deploys to Render.", supersedes=by_agent)["saved"]
    assert m.superseded_by(by_agent) == fixed
    assert ("supersede", "user", owner["id"]) in m.db.execute(
        "SELECT kind, source, via FROM evidence WHERE kind='supersede'").fetchall()
    act(srv, token, "tapestry_topics", action="describe", topic="atlas", description="The Atlas project")
    act(srv, token, "tapestry_file", memories=[fixed], add=["atlas"], reason="it's about atlas")
    assert [c["by"] for c in view(srv, "/api/changes?mind=will", token)[1]["changes"]] == ["user", "user"]
    undone = act(srv, token, "tapestry_changes", action="undo", change=2, reason="filed by mistake")
    assert "error" not in undone and m.memory(fixed)["topics"] == []


def test_forgetting_in_the_viewer_waits_for_the_owner_to_confirm(tmp_path, monkeypatch, srv):
    _, token = srv["reg"].add_owner()
    mid = act(srv, token, "tapestry_note", content="Will's locker code is 4417.", topics=[])["saved"]
    asked = act(srv, token, "tapestry_forget", memory=mid)
    assert asked["forgotten"] is False and "4417" in asked["says"]
    assert view(srv, f"/api/memory/{mid}?mind=will", token)[1]["content"] == "Will's locker code is 4417."
    assert act(srv, token, "tapestry_forget", memory=mid, confirmed=True)["forgotten"] is True
    assert view(srv, f"/api/memory/{mid}?mind=will", token)[1] == {"id": mid, "forgotten": True}


def test_a_change_the_viewer_cant_make_says_so_and_leaves_the_mind_as_it_was(tmp_path, srv):
    from tapestry import viewer
    _, token = srv["reg"].add_owner()
    old = act(srv, token, "tapestry_note", content="Atlas deploys to Fly.io.", topics=[])["saved"]
    act(srv, token, "tapestry_note", content="Atlas deploys to Render.", topics=[], supersedes=old)
    m = srv["app"].served("will").mind
    before = snapshot(m)
    for name, args in (("tapestry_note", {"content": "Atlas deploys to Heroku.", "supersedes": old}),
                       ("tapestry_forget", {"memory": 999, "confirmed": True}),
                       ("tapestry_recall", {"query": "atlas"}),
                       ("tapestry_topics", {"action": "open", "topic": "atlas"})):
        assert "error" in act(srv, token, name, **args), name
    assert snapshot(m) == before
    # The viewer `tapestry view` serves only looks.
    local = viewer.Viewer(m, name="will")
    assert "only looks" in local.act("tapestry_note", {"content": "Atlas has 40 tests.", "source": "user"})["error"]
    assert snapshot(m) == before


# -- Hermes as a client (docs/interfaces/HOOKS.md, docs/primitives/MIND.md) -------------------

def hermes_on(tmp_path, srv, name, *, mind="ash", session="h1", context="primary"):
    """A Hermes on its own machine, connected to the server through its own key."""
    from tapestry.hosts import hermes
    from tests.test_hermes import toy_factory
    key, token = srv["reg"].add_key(mind=mind, agent="ash", machine=name)
    home = tmp_path / f"hermes-{name}"
    remote.connect(home / "tapestry", srv["url"], token)
    p = hermes.TapestryProvider(encoder_factory=toy_factory)
    p.initialize(session, hermes_home=str(home), platform="cli", agent_context=context)
    p.key, p.home = key, home
    return p


def episode_turns(srv, session, mind="ash"):
    m = srv["app"].served(mind).mind
    return [(t["source"], t["text"]) for t in m.episode(session).turns()]


def test_hermes_on_one_machine_recalls_what_hermes_noted_on_another(tmp_path, srv):
    pi, laptop = hermes_on(tmp_path, srv, "pi"), hermes_on(tmp_path, srv, "laptop", session="h2")
    assert "saved" in pi.handle_tool_call("tapestry_note", {"content": "Sam keeps bees.", "source": "user", "topics": []})
    assert "'ash' on a Tapestry server" in laptop.system_prompt_block()
    assert "Sam keeps bees." in laptop.prefetch("Sam keeps bees?")
    assert laptop.recall_status() is None or laptop._last_count == 1
    assert srv["app"].served("will").mind.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_recall_through_the_server_is_what_the_same_mind_gives_hermes_on_this_machine(tmp_path, srv):
    """docs/interfaces/HOOKS.md: a behaviour that differs by how the mind is reached is a bug."""
    import os
    from tapestry.hosts import hermes
    from tests.test_hermes import toy_factory
    pi = hermes_on(tmp_path, srv, "pi", session="h1")
    for text in ("Sam keeps bees.", "Sam's hives are behind the garage.", "Atlas deploys to Fly.io."):
        pi.handle_tool_call("tapestry_note", {"content": text, "source": "user", "topics": []})
    asked = "Where are Sam's hives?"
    there = hermes_on(tmp_path, srv, "laptop", session="h8").prefetch(asked)
    local_home = tmp_path / "hermes-local"
    (local_home / "tapestry").mkdir(parents=True)
    os.symlink(srv["reg"].minds()["ash"], local_home / "tapestry" / "mind.db")
    here = hermes.TapestryProvider(encoder_factory=toy_factory)
    here.initialize("h9", hermes_home=str(local_home), platform="cli", agent_context="subagent")
    try:
        assert here.prefetch(asked) == there and "hives" in there
    finally:
        here.shutdown()


def test_turns_hermes_couldnt_send_are_held_said_and_added_once_the_server_is_back(tmp_path, srv, monkeypatch):
    """docs/primitives/MIND.md: a mind is behind only while a machine holds turns it couldn't add;
    that machine says so until they're added, and adds them as soon as it reaches the mind."""
    pi = hermes_on(tmp_path, srv, "pi")
    real = remote.call
    def down(server, what, body, **kw):
        raise remote.Unreachable("the memory server at test couldn't be reached (outage)")
    monkeypatch.setattr(remote, "call", down)
    pi.sync_turn("We moved the hives on Tuesday.", "Noted.", session_id="h1")
    pi.sync_turn("Sam wants a second hive.", "Got it.", session_id="h1")
    pi._sender.wait(5)
    said = pi.prefetch("What about the hives?")
    assert "MEMORY IS BEHIND: 4 turns" in said and "MEMORY UNAVAILABLE" in said
    assert episode_turns(srv, "h1") == []
    # A restart while the server is down loses nothing: the turns are on disk.
    pi.shutdown()
    monkeypatch.setattr(remote, "call", real)
    back = hermes_on(tmp_path, srv, "pi2")
    back._sender = pi._sender.__class__(pi._sender.outbox, back._server)   # pi's own outbox, as after a restart
    back._sender.kick()
    assert back._sender.wait(10)
    assert episode_turns(srv, "h1") == [("user", "We moved the hives on Tuesday."), ("agent", "Noted."),
                                        ("user", "Sam wants a second hive."), ("agent", "Got it.")]
    assert "MEMORY IS BEHIND" not in back.prefetch("What about the hives?")
    assert pi._sender.outbox.path.stat().st_mode & 0o777 == 0o600
    # Sending again adds nothing twice.
    for line in ('{"session": "h1", "place": null, "turns": [{"key": "%s", "source": "user", "text": "x", "ts": 1}]}'
                 % srv["app"].served("ash").mind.db.execute("SELECT key FROM turns LIMIT 1").fetchone()[0],):
        back._sender.outbox.add(json.loads(line))
    back._sender.kick()
    assert back._sender.wait(10) and len(episode_turns(srv, "h1")) == 4


def test_the_end_of_a_hermes_session_reaches_the_server_after_its_turns(tmp_path, srv):
    pi = hermes_on(tmp_path, srv, "pi")
    pi.on_memory_write("add", "memory", "Sam prefers raw honey.", {"session_id": "h1"})
    pi.sync_turn("Sam keeps bees.", "Nice.", session_id="h1")
    pi.on_session_end([])
    m = srv["app"].served("ash").mind
    assert m.db.execute("SELECT ended_at IS NOT NULL FROM episodes WHERE id='h1'").fetchone() == (1,)
    assert episode_turns(srv, "h1")[-1] == ("agent", "Sam prefers raw honey.")
    assert {r[0] for r in m.db.execute("SELECT via FROM turns")} == {pi.key["id"]}


def test_a_hermes_subagent_on_a_server_records_nothing_of_its_session(tmp_path, srv):
    main = hermes_on(tmp_path, srv, "pi")
    main.handle_tool_call("tapestry_note", {"content": "Sam keeps bees.", "source": "user", "topics": []})
    sub = hermes_on(tmp_path, srv, "pi-sub", session="h2", context="subagent")
    assert "Sam keeps bees." in sub.prefetch("Sam keeps bees?")
    sub.sync_turn("hello", "hi", session_id="h2")
    sub.on_session_end([])
    m = srv["app"].served("ash").mind
    assert m.db.execute("SELECT count(*) FROM recalled WHERE episode='h2'").fetchone()[0] == 0
    assert m.db.execute("SELECT count(*) FROM turns WHERE episode='h2'").fetchone()[0] == 0
    assert not sub._sender.outbox.pending()


def test_hermes_on_a_server_that_cant_be_reached_is_memory_unavailable_never_empty(tmp_path):
    from tapestry.hosts import hermes
    home = tmp_path / "hermes"
    (home / "tapestry").mkdir(parents=True)
    (home / "tapestry" / "remote.json").write_text(json.dumps({"url": "http://127.0.0.1:9", "token": "tap_x"}))
    p = hermes.TapestryProvider()
    assert p.is_available()
    p.initialize("h1", hermes_home=str(home), platform="cli")
    assert "MEMORY UNAVAILABLE" in p.system_prompt_block()
    assert p.prefetch("Where are Sam's hives?").startswith("[MEMORY UNAVAILABLE")
    assert "couldn't be reached" in json.loads(p.handle_tool_call("tapestry_recall", {"query": "hives"}))["error"]
