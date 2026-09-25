"""The harness has to be trustworthy before anything is measured with it."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench import fixture, run
from bench.metrics import Hit, brier, ece, score_query
from bench.systems.bm25 import BM25

FIXTURES = Path(__file__).resolve().parent.parent / "bench" / "fixtures"


def _fx(tmp_path: Path, toml: str) -> fixture.Fixture:
    p = tmp_path / "fx.toml"
    p.write_text(textwrap.dedent(toml))
    return fixture.load(p)


CHAIN = """
    name = "chain"
    [[memory]]
    id = "a"
    content = "first"
    [[memory]]
    id = "b"
    content = "second"
    supersedes = "a"
    [[memory]]
    id = "c"
    content = "third"
    supersedes = "b"
    [[memory]]
    id = "p"
    scope = "proj"
    content = "project only"
    [[query]]
    text = "which?"
    expect = ["c"]
    scopes = ["user"]
    [[query]]
    text = "nothing"
"""


class Oracle:
    """Serves exactly the right answer: the harness's ceiling."""

    name = "oracle"

    def __init__(self, fx: fixture.Fixture) -> None:
        # Keyed by text and loaded scopes: the same question can have different
        # right answers depending on which project is loaded.
        self.answers = {(q.text, fx.loaded_scopes(q)): q.expect for q in fx.queries}

    def config(self):
        return {}

    def seed(self, memories):
        pass

    def recall(self, text, *, scopes, k, at):
        return [Hit(id=m, score=1.0) for m in self.answers[(text, scopes)]]

    def close(self):
        pass


def test_shipped_fixtures_load_and_validate():
    loaded = fixture.load_dir(FIXTURES)
    assert {f.name for f in loaded} >= {"basic", "supersession", "scopes"}
    assert all(f.queries for f in loaded)


def test_stale_ids_follow_supersession_transitively(tmp_path):
    fx = _fx(tmp_path, CHAIN)
    assert fx.stale_ids(fx.queries[0]) == {"a", "b"}
    assert fx.stale_ids(fx.queries[1]) == frozenset()


@pytest.mark.parametrize("bad, message", [
    ('[[memory]]\nid = "x"\ncontent = "."\nsupersedes = "ghost"', "supersedes unknown"),
    ('[[memory]]\nid = "x"\ncontent = "."\n[[query]]\ntext = "?"\nexpect = ["ghost"]', "unknown ghost"),
    ('[[memory]]\nid = "x"\nscope = "p"\ncontent = "."\n[[memory]]\nid = "y"\ncontent = "."\n'
     '[[query]]\ntext = "?"\nexpect = ["x"]\nscopes = ["user"]', "unloaded scope"),
    ('[[memory]]\nid = "x"\ncontent = "."\n[[memory]]\nid = "x"\ncontent = ","', "duplicate"),
])
def test_invalid_fixtures_are_rejected(tmp_path, bad, message):
    with pytest.raises(ValueError, match=message):
        _fx(tmp_path, bad)


def test_score_query_flags_stale_leaked_and_rank(tmp_path):
    fx = _fx(tmp_path, CHAIN)
    q = fx.queries[0]
    r = score_query(fx, q, [Hit("b", .9), Hit("p", .8), Hit("c", .7)], k=5)
    assert r.rank == 3 and r.recall == 1.0
    assert r.stale_served == ["b"]
    assert r.leaked == ["p"]
    assert r.scope_restricted and r.stale_applicable


def test_k_truncates_before_scoring(tmp_path):
    fx = _fx(tmp_path, CHAIN)
    r = score_query(fx, fx.queries[0], [Hit("a", 1), Hit("c", .5)], k=1)
    assert r.rank is None and r.recall == 0.0 and r.stale_served == ["a"]


def test_oracle_scores_perfectly_on_every_fixture():
    for fx in fixture.load_dir(FIXTURES):
        s = run.run_fixture(Oracle(fx), fx, k=5).summary()
        assert s["recall@5"] == 1.0 and s["mrr"] == 1.0
        assert s["stale_serve"] in (0.0, None)
        assert s["leakage"] in (0.0, None)
        assert s["negative_serve"] in (0.0, None)


def test_bm25_runs_end_to_end():
    fx = fixture.load(FIXTURES / "basic.toml")
    bm = BM25()
    try:
        s = run.run_fixture(bm, fx, k=5).summary()
    finally:
        bm.close()
    assert 0.0 <= s["recall@5"] <= 1.0
    assert s["negative_confident"] is None  # bm25 makes no confidence claims


def test_calibration_metrics():
    assert brier([(1.0, True), (0.0, False)]) == 0.0
    assert brier([(1.0, False)]) == 1.0
    assert ece([(0.8, True)] * 8 + [(0.8, False)] * 2) == 0.0
    assert ece([(0.9, False)] * 10) == 0.9
    assert brier([]) is None and ece([]) is None


def _lme_item(qid="q1", has_answer=True):
    return {
        "question_id": qid, "question_type": "knowledge-update",
        "question": "Where do I live now?", "answer": "Lisbon",
        "question_date": "2023/05/30 (Tue) 23:40",
        "haystack_session_ids": ["s1", "s2", "s2"],
        "haystack_dates": ["2023/05/01 (Mon) 10:00", "2023/05/20 (Sat) 02:21",
                           "2023/05/20 (Sat) 02:21"],
        "haystack_sessions": [
            [{"role": "user", "content": "I live in Porto."}],
            [{"role": "user", "content": "I moved to Lisbon.", "has_answer": has_answer},
             {"role": "assistant", "content": "Congrats!"}],
            [{"role": "user", "content": "duplicate"}],
        ],
        "answer_session_ids": ["s2"],
    }


def test_longmemeval_session_fixture():
    from bench import longmemeval
    fx = longmemeval.to_fixture(_lme_item())
    assert fx.name == "lme:knowledge-update"
    assert [m.id for m in fx.memories] == ["s1", "s2"]  # repeated session dropped
    assert fx.queries[0].expect == ("s2",)
    assert fx.memories[1].content.startswith("user: I moved to Lisbon.")


def test_longmemeval_turn_fixture_and_abstention():
    from bench import longmemeval
    fx = longmemeval.to_fixture(_lme_item(), granularity="turn")
    assert fx.queries[0].expect == ("s2#0",)
    neg = longmemeval.to_fixture(_lme_item(qid="q1_abs"))
    assert neg.name == "lme:abstention" and neg.queries[0].is_negative
