"""The viewer, through the HTTP surface an owner's browser uses (docs/interfaces/VIEWER.md)."""

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tapestry import recall, render, unrecorded, viewer
from tapestry.mind import Mind, Unreadable
from tests.test_mind import toy_encode


def _bytes_of(path: Path) -> bytes:
    """Everything a mind has on disk that holds what it remembers: the database and its log."""
    return b"".join(f.read_bytes() for f in (path, path.with_name(path.name + "-wal")) if f.exists())


@pytest.fixture
def written(tmp_path):
    """A mind with topics, associations, a correction, a conflict, words it was drawn from,
    and one forgotten memory. Returns its path and the ids that matter."""
    path = tmp_path / "m.db"
    m = Mind(path, encode=toy_encode, model_tag="toy")
    m.topic("garden", "The vegetable garden: beds, plantings and pests.")
    m.topic("finance", "Money: the household budget and bills.")
    ep = m.episode("s1", "garden")
    ep.add_turn("t1", "user", "Bed three grows tomatoes this year, by the way.", 1.0)
    ids = {}
    ids["tomatoes"] = m.remember("Bed three grows tomatoes.", source="user", topics=["garden"],
                                 episode="s1", drawn_from=["t1"])
    ids["aphids"] = m.remember("Aphids got the tomatoes in bed three last July.", source="agent",
                               topics=["garden"], associate=[ids["tomatoes"]])
    ids["marigolds"] = m.remember("Marigolds deter whiteflies.", source="user",
                                  topics=["garden"], associate=[ids["tomatoes"]])
    ids["rent"] = m.remember("Rent is $1,900 a month.", source="user", topics=["finance"])
    ids["rent_now"] = m.remember("Rent is $2,050 a month.", source="user", topics=["finance"],
                                 supersedes=ids["rent"], associate=[ids["rent"]])
    ids["pink"] = m.remember("Sam's favorite color is pink.", source="user")
    ids["green"] = m.remember("Sam's favorite color is green.", source="agent")
    m.record_judgments(ids["pink"], prompt="p", model="toy", categories={ids["green"]: "conflict"})
    ids["gone"] = m.remember("Sam's locker code is qwrtzp4417.", source="user")
    m.forget(ids["gone"])
    m.close()
    return path, ids


@pytest.fixture
def view(written, tmp_path):
    path, ids = written
    behind = unrecorded.Ledger(tmp_path / "behind.jsonl")
    behind.add("drawing facts from s1 failed (the model timed out)")
    mind = Mind.reading(path, encode=toy_encode, model_tag="toy")
    server = viewer.serve(viewer.Viewer(mind, notices=viewer.notices(
        behind, unrecorded.Ledger(tmp_path / "unrecorded.jsonl"), tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(route, *, token=server.token, host=None):
        req = urllib.request.Request(base + route, headers={"X-Tapestry-Token": token, **({"Host": host} if host else {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def api(route, **kw):
        status, body = get(route, **kw)
        return status, json.loads(body)

    yield {"path": path, "ids": ids, "get": get, "api": api, "behind": behind, "server": server}
    server.shutdown()
    server.server_close()
    mind.close()


def _everything(view) -> list[str]:
    """Every route the viewer serves, with the moments and memories this mind holds."""
    ids = view["ids"]
    return (["/", "/api/status", "/api/memories?topics=general,garden,finance&superseded=1",
             "/api/memories?topics=garden", "/api/recall?q=what%20grows%20in%20bed%20three&topics=garden,finance",
             "/api/recall?q=favorite%20color"]
            + [f"/api/memory/{i}" for i in ids.values()])


def test_the_viewer_never_changes_a_mind(view):
    before = _bytes_of(view["path"])
    for route in _everything(view):
        status, _ = view["get"](route)
        assert status in (200, 404), route
    assert _bytes_of(view["path"]) == before
    # What the next session is to be told about work that's behind is still waiting for it.
    assert view["behind"].peek() == ["drawing facts from s1 failed (the model timed out)"]


def test_the_store_refuses_every_write_through_a_mind_opened_for_reading(written):
    path, ids = written
    mind = Mind.reading(path, encode=toy_encode, model_tag="toy")
    try:
        for write in (lambda: mind.remember("Sam keeps bees.", source="user"),
                      lambda: mind.confirm(ids["tomatoes"], source="user"),
                      lambda: mind.strengthen([ids["tomatoes"], ids["aphids"]]),
                      lambda: mind.topic("bees", "Sam's bees")):
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
                write()
    finally:
        mind.close()


def test_a_mind_it_cant_show_without_changing_it_is_refused_and_says_why(tmp_path):
    old = tmp_path / "old.db"
    db = sqlite3.connect(old)
    db.executescript((Path(__file__).parent / "fixtures" / "mind_v2.sql").read_text())
    db.close()
    before = sorted(p.name for p in tmp_path.iterdir()), old.read_bytes()
    with pytest.raises(Unreadable, match="older version"):
        Mind.reading(old, encode=toy_encode, model_tag="toy")
    assert (sorted(p.name for p in tmp_path.iterdir()), old.read_bytes()) == before   # not upgraded, no copy made
    with pytest.raises(Unreadable, match="no mind"):
        Mind.reading(tmp_path / "missing.db", encode=toy_encode, model_tag="toy")
    assert not (tmp_path / "missing.db").exists()                                      # and none created


def test_it_shows_the_mind_only_to_whoever_holds_its_address(view):
    assert view["get"]("/api/status", token="")[0] == 403
    assert view["get"]("/api/status", token="guess")[0] == 403
    # A page elsewhere that points its own name at this machine still can't read it.
    assert view["get"]("/api/status", host="attacker.example:80")[0] == 403
    assert view["server"].server_address[0] == "127.0.0.1"


def test_nothing_the_page_shows_can_leave_the_machine(view):
    req = urllib.request.Request(f"http://127.0.0.1:{view['server'].server_address[1]}/",
                                 headers={"X-Tapestry-Token": view["server"].token})
    with urllib.request.urlopen(req) as r:
        csp, page = r.headers["Content-Security-Policy"], r.read()
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp
    assert b"http://" not in page and b"https://" not in page          # it loads nothing from elsewhere


def test_memories_are_labelled_in_the_words_the_agent_is_given(view, written):
    path, ids = written
    _, shown = view["api"](f"/api/memory/{ids['aphids']}")
    mind = Mind(path, encode=toy_encode, model_tag="toy")
    try:
        hit = next(r for r in mind.recall("aphids tomatoes bed three", topics=["garden"]) if r.id == ids["aphids"])
    finally:
        mind.close()
    assert render.line(hit).count(shown["belief"]["why"]) == 1
    assert f"belief: {shown['belief']['word']} ({shown['belief']['p']:.2f})" in render.line(hit)
    assert shown["belief"]["word"] not in ("ok", "LOW", "keyword only")   # never in match's words


def test_trying_recall_shows_exactly_what_the_agent_would_have_been_given(view, written):
    path, ids = written
    moment = "what grows in bed three"
    _, shown = view["api"]("/api/recall?q=what%20grows%20in%20bed%20three&topics=garden")
    mind = Mind(path, encode=toy_encode, model_tag="toy")
    try:
        given = render.automatic(recall.before_turn(mind, moment, ["garden"]))
    finally:
        mind.close()
    assert shown["as_given"] == given and given
    assert shown["searched"] == ["general", "garden"]
    brought = [r for r in shown["found"] if r["brought_by"] is not None]
    assert brought and all(r["match"] is None for r in brought)       # reached, not matched, and says by what


def test_a_superseded_memory_is_never_shown_as_current(view):
    ids = view["ids"]
    _, current = view["api"]("/api/memories?topics=finance")
    assert [m["id"] for m in current["memories"]] == [ids["rent_now"]]
    _, everything = view["api"]("/api/memories?topics=finance&superseded=1")
    old = next(m for m in everything["memories"] if m["id"] == ids["rent"])
    assert old["superseded_by"] == ids["rent_now"]
    _, detail = view["api"](f"/api/memory/{ids['rent_now']}")
    assert [m["id"] for m in detail["supersedes"]] == [ids["rent"]]
    _, found = view["api"]("/api/recall?q=rent%20a%20month&topics=finance")
    assert ids["rent"] not in [r["id"] for r in found["found"]]


def test_two_memories_in_conflict_are_shown_together(view):
    ids = view["ids"]
    _, detail = view["api"](f"/api/memory/{ids['green']}")
    assert [c["id"] for c in detail["conflicts"]] == [ids["pink"]]
    _, found = view["api"]("/api/recall?q=Sam%20favorite%20color%20pink")
    assert {ids["pink"], ids["green"]} <= {r["id"] for r in found["found"]}


def test_an_association_shows_its_strength_never_as_belief_and_every_memory_its_topics(view):
    ids = view["ids"]
    _, web = view["api"]("/api/memories?topics=general,garden")
    assert {"from": ids["aphids"], "to": ids["tomatoes"]}.items() <= next(
        a for a in web["associations"] if a["from"] == ids["aphids"]).items()
    assert all(set(a) == {"from", "to", "strength"} for a in web["associations"])
    assert {m["id"]: m["topics"] for m in web["memories"]}[ids["pink"]] == ["general"]
    _, detail = view["api"](f"/api/memory/{ids['tomatoes']}")
    led = next(m for m in detail["led_from"] if m["id"] == ids["aphids"])
    assert 0 < led["strength"] <= 1
    _, aphids = view["api"](f"/api/memory/{ids['aphids']}")
    assert led["belief"] == aphids["belief"]            # its own belief, whatever the strength leading to it


def test_an_episode_appears_only_as_where_a_memory_came_from(view):
    ids = view["ids"]
    _, detail = view["api"](f"/api/memory/{ids['tomatoes']}")
    assert detail["came_from"] == [{"source": "user", "text": "Bed three grows tomatoes this year, by the way.",
                                    "when": render.learned(1.0)}]
    for route in _everything(view):
        if f"/api/memory/{ids['tomatoes']}" not in route:
            assert b"this year, by the way" not in view["get"](route)[1], route


def test_a_forgotten_memory_leaves_no_trace_beyond_the_fact_it_was_forgotten(view):
    status, gone = view["api"](f"/api/memory/{view['ids']['gone']}")
    assert status == 200 and gone == {"id": view["ids"]["gone"], "forgotten": True}
    for route in _everything(view):
        assert b"qwrtzp4417" not in view["get"](route)[1], route


def test_a_viewer_that_cant_read_a_mind_says_so(view, written, tmp_path):
    _, status = view["api"]("/api/status")
    assert any("BACKGROUND WORK IS BEHIND" in n for n in status["notices"])
    # A mind whose memories can't be searched says recall failed, never "nothing found".
    path, _ = written
    other = Mind.reading(path, encode=toy_encode, model_tag="another-model")
    try:
        out = viewer.Viewer(other).recall("bed three", ["garden"])
    finally:
        other.close()
    assert "error" in out and "nothing was searched" in out["error"] and "found" not in out
