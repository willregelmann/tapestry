"""The highlight sample (PLAN.md stage 6, measure 2): rating highlights never changes the mind."""

from __future__ import annotations

import json
import random

from tapestry import sample
from tapestry.mind import Mind
from tests.test_mind import toy_encode

NOW = 1_800_000_000.0


def a_mind(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    ep = m.episode("s1", None)
    tea = m.remember("Sam likes tea.", source="user", at=NOW - 3600)
    old = m.remember("Sam likes coffee.", source="user", at=NOW - 30 * 86400)
    ep.add_turn("s1:0", "user", "What should I bring Sam?", NOW - 60)
    ep.add_recalled([tea], ts=NOW - 60)
    ep.add_recalled([old], ts=NOW - 20 * 86400)       # outside the window
    return m, tea


def snapshot(m):
    return [m.db.execute(f"SELECT * FROM {t}").fetchall() for t in ("memories", "evidence", "associations")]


def test_only_highlights_from_the_window_are_picked_with_their_moment(tmp_path):
    m, tea = a_mind(tmp_path)
    items = sample.pick(m, 10, days=7, now=NOW, rng=random.Random(0))
    assert [i["memory"] for i in items] == [tea] and items[0]["moment"] == "What should I bring Sam?"


def test_rating_highlights_never_changes_the_mind_and_keeps_each_answer(tmp_path):
    m, tea = a_mind(tmp_path)
    before = snapshot(m)
    answers = iter(["y", "n"])
    kept = sample.rate(sample.pick(m, 10, now=NOW), lambda _: next(answers), tmp_path / "samples.jsonl")
    assert snapshot(m) == before
    assert kept[0]["relevant"] is True and kept[0]["true"] is False
    assert [json.loads(l)["memory"] for l in (tmp_path / "samples.jsonl").read_text().splitlines()] == [tea]


def test_precision_is_the_share_rated_both_relevant_and_still_true(tmp_path):
    log = tmp_path / "samples.jsonl"
    rows = [(True, True), (True, False), (False, True), (True, True)]
    log.write_text("".join(json.dumps({"rated_at": NOW - 10, "relevant": r, "true": t}) + "\n" for r, t in rows))
    out = sample.report(log, now=NOW)
    assert "2/4 relevant and still true (50%)" in out and "small sample" in out


def test_a_skipped_highlight_is_not_kept(tmp_path):
    m, _ = a_mind(tmp_path)
    assert sample.rate(sample.pick(m, 10, now=NOW), lambda _: "s", tmp_path / "s.jsonl") == []


def test_quitting_keeps_what_was_rated_so_far(tmp_path):
    m, _ = a_mind(tmp_path)
    def ask(_):
        raise EOFError
    assert sample.rate(sample.pick(m, 10, now=NOW), ask, tmp_path / "s.jsonl") == []


def test_the_moment_is_found_when_the_turn_is_recorded_after_its_recall(tmp_path):
    m = Mind(tmp_path / "m.db", encode=toy_encode, model_tag="toy")
    ep = m.episode("s1", None)
    tea = m.remember("Sam likes tea.", source="user", at=NOW - 3600)
    ep.add_recalled([tea], ts=NOW - 60)
    ep.add_turn("s1:0", "user", "What should I bring Sam?", NOW - 55)    # recorded after recalling
    assert sample.pick(m, 1, now=NOW)[0]["moment"] == "What should I bring Sam?"
