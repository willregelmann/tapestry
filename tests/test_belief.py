"""Belief is read from evidence (docs/primitives/MEMORY.md), never stored or granted."""

from __future__ import annotations

import json

import pytest

from tapestry import belief, render
from tapestry.mind import Mind
from tests.test_mind import toy_encode

DAY, YEAR = belief.DAY, belief.YEAR


def ev(kind, source, ts, episode=None, strength=1.0):
    return {"id": int(ts * 10), "kind": kind, "source": source, "episode": episode,
            "strength": strength, "ts": ts}


def read(events, volatility="preference", now=0.0, **kw):
    return belief.read(events, volatility, now, **kw)


def test_a_memory_is_believed_as_far_as_its_source_is_reliable():
    assert read([ev("observe", "user", 0)]).p == pytest.approx(0.95)
    assert read([ev("observe", "web", 0)]).p == pytest.approx(0.75)
    assert read([ev("observe", "user", 0)]).p > read([ev("observe", "agent", 0)]).p


def test_belief_fades_without_fresh_evidence_at_a_rate_set_by_the_kind_of_thing():
    seen = [ev("observe", "user", 0)]
    pref = read(seen, "preference", now=YEAR).p
    assert pref == pytest.approx(0.95 / 2, rel=1e-6)                   # one half-life
    assert read(seen, "immutable", now=YEAR).p > 0.93                  # a name barely fades
    assert read(seen, "project", now=YEAR).p < 0.05                    # a plan expires
    assert read(seen, "transient", now=30 * DAY).p < 0.06              # a session's work fades in days
    assert read(seen, "immutable", now=YEAR).p > pref > read(seen, "project", now=YEAR).p


def test_nothing_renews_itself_only_evidence_does():
    seen = [ev("observe", "user", 0)]
    assert read(seen, now=200 * DAY).p < read(seen, now=100 * DAY).p
    again = seen + [ev("confirm", "user_confirmed", 200 * DAY)]
    assert read(again, now=200 * DAY).p > read(seen, now=200 * DAY).p


def test_a_confirmation_after_years_of_silence_does_not_restore_certainty():
    # The record fades between entries, so an old high reading doesn't prop up a late confirmation.
    late = read([ev("observe", "user", 0), ev("confirm", "user_confirmed", 2 * YEAR)], now=2 * YEAR).p
    assert 0.8 < late < 0.97


def test_belief_is_hard_to_move_one_weak_contradiction_does_not_topple_a_long_record():
    record = [ev("observe", "user", 0)] + [ev("confirm", "user_confirmed", 90 * DAY * i) for i in range(1, 9)]
    at = 720 * DAY
    before = read(record, now=at).p
    weak = read(record + [ev("contradict", "inference", at)], now=at).p
    strong = read(record + [ev("contradict", "user", at)], now=at).p
    assert before > 0.95 and weak > 0.9
    assert strong < weak


def test_belief_is_never_certain_however_long_the_record():
    record = [ev("observe", "user", 0)] + [ev("confirm", "user_confirmed", DAY * i, episode=str(i)) for i in range(1, 200)]
    assert read(record, "immutable", now=200 * DAY).p < 0.999


def test_the_same_source_repeating_in_one_episode_counts_once():
    once = [ev("observe", "user", 0), ev("confirm", "user", DAY, episode="s1")]
    echoed = once + [ev("confirm", "user", DAY + i, episode="s1") for i in range(1, 20)]
    assert read(echoed, now=DAY).p == pytest.approx(read(once, now=DAY).p)
    elsewhere = once + [ev("confirm", "user", 2 * DAY, episode="s2")]
    assert read(elsewhere, now=2 * DAY).p > read(once, now=2 * DAY).p


def test_recording_the_same_observation_again_is_not_a_second_report():
    one = [ev("observe", "user", 0)]
    again = one + [ev("observe", "user", 200 * DAY, episode="s2")]
    assert read(again, now=200 * DAY).p == pytest.approx(read(one, now=200 * DAY).p)


def test_where_a_memory_lives_does_not_move_belief():
    seen = [ev("observe", "user", 0)]
    moved = seen + [ev("file", "agent", DAY), ev("unfile", "agent", 2 * DAY), ev("supersede", "agent", 3 * DAY)]
    assert read(moved, now=10 * DAY).p == pytest.approx(read(seen, now=10 * DAY).p)


def test_replaying_the_evidence_rebuilds_belief_whatever_order_it_is_read_in():
    events = [ev("observe", "user", 0), ev("confirm", "tool", 10 * DAY),
              ev("contradict", "web", 40 * DAY), ev("confirm", "user_confirmed", 90 * DAY)]
    assert read(list(reversed(events)), now=120 * DAY) == read(events, now=120 * DAY)


def test_changing_how_much_a_source_is_trusted_changes_the_reading_never_the_evidence():
    events = [ev("observe", "web", 0), ev("confirm", "web", DAY)]
    snapshot = json.dumps(events)
    trusting = {**belief.RELIABILITY, "web": 0.95}
    assert read(events, now=DAY, reliability=trusting).p > read(events, now=DAY).p
    assert json.dumps(events) == snapshot


def test_belief_words_never_reuse_the_words_match_uses():
    words = {read([ev("observe", "user", 0)], now=t * DAY).word for t in (0, 200, 400, 1500)}
    assert words <= {"very likely", "likely", "uncertain", "doubtful"} and len(words) > 1
    assert not words & {"ok", "LOW", "keyword only"}


# -- through a mind ------------------------------------------------------------

@pytest.fixture
def mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    yield m
    m.close()


def test_every_recalled_result_carries_match_and_belief_as_separate_labels(mind):
    mid = mind.remember("Sam's favorite color is pink.", source="user", at=1.0, volatility="preference")
    [hit] = mind.recall("favorite color", namespaces=[])
    text = render.line(hit)
    assert "match: " in text and "belief: " in text
    assert hit.belief is not None and hit.belief.origin == "user"
    assert "the user said it" in text and "preference" in text


def test_belief_is_never_stored_on_the_memory(mind):
    mid = mind.remember("Sam likes tea.", source="user")
    mind.confirm(mid, source="user_confirmed")
    mind.recall("tea", namespaces=[])
    assert mind.db.execute("SELECT belief_L, belief_t FROM memories").fetchone() == (None, None)


def test_a_confirmation_raises_belief_and_a_contradiction_lowers_it_and_says_so(mind):
    mid = mind.remember("Sam lives in Austin.", source="web", at=0.0, volatility="stable")
    base = mind.belief(mid, now=DAY).p
    mind.confirm(mid, source="user_confirmed", at=DAY)
    assert mind.belief(mid, now=DAY).p > base
    mind.contradict(mid, source="user", at=2 * DAY)
    b = mind.belief(mid, now=2 * DAY)
    assert b.contradicted and "contradicted since" in belief.why(b, 2 * DAY)


def test_recall_alone_never_moves_belief(mind):
    mid = mind.remember("Sam likes tea.", source="user", at=0.0)
    before = mind.belief(mid, now=DAY)
    for _ in range(5):
        mind.recall("Sam tea", namespaces=[])
    assert mind.belief(mid, now=DAY) == before


def test_a_superseded_memory_is_not_checked_any_more(mind):
    old = mind.remember("Sam's favorite color is pink.", source="user", at=1.0)
    mind.remember("Sam's favorite color is teal.", source="user", at=2.0, supersedes=old)
    with pytest.raises(ValueError, match="superseded"):
        mind.confirm(old, source="user_confirmed")


def test_a_volatility_that_is_not_a_known_kind_is_refused(mind):
    with pytest.raises(ValueError, match="volatility"):
        mind.remember("Sam likes tea.", source="user", volatility="forever")


def test_a_memory_inferred_by_the_agent_starts_less_believed_than_one_the_user_said(mind):
    said = mind.remember("Sam likes tea.", source="user", at=0.0)
    guessed = mind.remember("Sam likes coffee.", source="inference", at=0.0)
    assert mind.belief(said, now=0.0).p > mind.belief(guessed, now=0.0).p
