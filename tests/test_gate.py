"""bench.gate: corpus, Jev client (against a stub, never the network), and scoring."""

import json
import sqlite3

import pytest

from bench.gate import corpus, jev, score

BLOCK = """<memory-context>
[System note: The following is recalled memory context, NOT new user input.]

[#12 · match: ok · cos 0.71 · learned Oct 2026] Merge with --match-head-commit.
Second line of the same memory.
[#7 · session.api_1 · match: LOW · cos 0.31 · learned Oct 2026] Buster runs on Sonnet.
[#3 · match: keyword only · cos 0.10 · learned Oct 2026] Keyword hit.
</memory-context>"""


def _db(tmp_path, rows):
    p = tmp_path / "state.db"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
               " content TEXT, api_content TEXT)")
    db.executemany("INSERT INTO messages (id, session_id, role, content, api_content) VALUES (?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return p


def test_items_parse_every_candidate_in_order_with_cosine_and_label():
    got = corpus.items(corpus._BLOCK.search(BLOCK).group(1))
    assert [g["memory"] for g in got] == [12, 7, 3]
    assert got[0]["text"] == "Merge with --match-head-commit.\nSecond line of the same memory."
    assert [g["cosine"] for g in got] == [0.71, 0.31, 0.10]
    assert [g["match"] for g in got] == ["ok", "LOW", "keyword only"]
    assert [g["rank"] for g in got] == [1, 2, 3]


def test_turns_keep_real_requests_drop_alarms_compaction_and_truncation(tmp_path):
    rows = [
        (1, "s", "user", "Please review #108", "Please review #108\n" + BLOCK),
        (2, "s", "assistant", "Looking at it now.", None),   # interim text before a tool call
        (3, "s", "assistant", "Reviewed: one finding.", None),
        (4, "s", "user", "[Alarm a_1 · you set this]", "[Alarm a_1]\n" + BLOCK),
        (5, "s", "user", "[CONTEXT COMPACTION] ...", "x\n" + BLOCK),
        (6, "s", "user", "[from ash · woken by its own alarm a_2]\n\nhi", "y\n" + BLOCK),
        (7, "s", "user", "Big one", "Big one\n<memory-context>[tapestry memory prefetch output truncated"
                                    " — 11,498 chars]</memory-context>"),
        (8, "s", "user", "no recall here", "no recall here"),
        (9, "s", "user", "tools only", "tools only\n" + BLOCK),
        (10, "s", "assistant", "", None),
    ]
    got, dropped = corpus.turns(sqlite3.connect(_db(tmp_path, rows)))
    assert [t["turn"] for t in got] == ["s:1"]
    assert got[0]["reply"] == "Reviewed: one finding."
    assert dropped == {"truncated": 1, "own_alarm_or_compaction": 3, "no_items": 0, "no_reply": 1}


def test_the_labeller_sheet_never_shows_cosine_or_match(tmp_path):
    rows = [(1, "s", "user", "Please review #108", "Please review #108\n" + BLOCK),
            (2, "s", "assistant", "Done.", None)]
    m = corpus.build(_db(tmp_path, rows), tmp_path / "out", n_turns=5, seed=1)
    assert m["candidates"] == 3 and m["turns_sampled"] == 1
    sheet = [json.loads(x) for x in (tmp_path / "out" / "sheet.jsonl").read_text().splitlines()]
    assert set(sheet[0]) == {"item", "message", "memory", "reply", "constrains"}
    full = [json.loads(x) for x in (tmp_path / "out" / "corpus.jsonl").read_text().splitlines()]
    assert [f["item"] for f in full] == [s["item"] for s in sheet]


def test_the_sample_is_fixed_by_its_seed(tmp_path):
    rows = []
    for i in range(1, 41, 2):
        rows += [(i, "s", "user", f"msg {i}", f"msg {i}\n" + BLOCK), (i + 1, "s", "assistant", "ok", None)]
    p = _db(tmp_path, rows)
    a = corpus.build(p, tmp_path / "a", n_turns=5, seed=7)
    b = corpus.build(p, tmp_path / "b", n_turns=5, seed=7)
    c = corpus.build(p, tmp_path / "c", n_turns=5, seed=8)
    ra, rb, rc = ((tmp_path / d / "corpus.jsonl").read_text() for d in "abc")
    assert ra == rb and ra != rc and a["turns_eligible"] == 20


def test_jev_sees_the_message_and_the_memory_never_reply_cosine_or_label(tmp_path):
    rows = [(1, "s", "user", "Please review #108", "Please review #108\n" + BLOCK),
            (2, "s", "assistant", "REPLY-SENTINEL reviewed", None)]
    corpus.build(_db(tmp_path, rows), tmp_path / "out", n_turns=5, seed=1)
    c = [json.loads(x) for x in (tmp_path / "out" / "corpus.jsonl").read_text().splitlines()]
    assert all("reply" not in r for r in c)            # the scorer's input never carries the reply
    body = jev.request(c[0]["message"], [(r["item"], r["memory"]) for r in c])
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"message": "Please review #108"}
    q = body["questions"]["c0"]
    assert q["type"] == "noul" and q["instructions"]["memory"].startswith("Merge with --match-head-commit.")
    text = json.dumps(body)
    for leak in ("REPLY-SENTINEL", "0.71", "0.31", "cosine", "keyword only", "match: "):
        assert leak not in text


def test_jev_maps_answers_back_to_items_and_records_the_answering_model(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test\n")
    sent = []

    def fake(body):
        sent.append(body)
        return {"model": "jev-1.13.0", "usage": {"input_tokens": 10},
                "answers": {"c0": {"type": "noul", "noul": 0.9}, "c1": {"type": "noul", "noul": 0.2}}}

    got = jev.Jev(key, send=fake).gate("m", [("a", "x"), ("b", "y")])
    assert got["p"] == {"a": 0.9, "b": 0.2} and got["model"] == "jev-1.13.0"
    assert len(sent) == 1 and len(sent[0]["questions"]) == 2


def test_a_malformed_answer_is_an_error_never_a_probability(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test")
    bad = {"answers": {"c0": {"type": "choice", "choice": "yes"}}}
    with pytest.raises(RuntimeError, match="isn't a noul"):
        jev.Jev(key, send=lambda b: bad).gate("m", [("a", "x")])


def test_an_empty_key_file_is_refused(tmp_path):
    key = tmp_path / "key"
    key.write_text("\n")
    with pytest.raises(ValueError, match="empty"):
        jev.Jev(key, send=lambda b: {})


def test_split_puts_every_turn_on_exactly_one_side():
    turns = [f"t{i}" for i in range(11)] * 3
    dev, test = score.split(turns, seed=3)
    assert dev.isdisjoint(test) and dev | test == set(turns) and len(dev) == 5


def test_threshold_is_the_highest_that_keeps_ninety_percent_of_positives():
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.95]
    labels = [True] * 10 + [False]
    assert score.threshold(scores, labels) == 0.1     # 9 of 10 positives kept
    assert sum(s >= 0.1 for s, y in zip(scores, labels) if y) == 9


def test_mcnemar_exact_matches_the_binomial():
    a = [True] * 8 + [False] * 2 + [True] * 5
    b = [False] * 8 + [True] * 2 + [True] * 5
    got = score.mcnemar(a, b)
    assert (got["only_a"], got["only_b"]) == (8, 2)
    assert got["p"] == pytest.approx(0.109375)          # 2 * P(X <= 2), X ~ Bin(10, 1/2)
    assert score.mcnemar([True], [True])["p"] == 1.0


def test_auroc_and_kappa_known_values():
    assert score.auroc([0.9, 0.8, 0.1], [True, True, False]) == 1.0
    assert score.auroc([0.1, 0.9], [True, False]) == 0.0
    assert score.auroc([0.5, 0.5], [True, False]) == 0.5
    assert score.kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert score.kappa([True, True, False, False], [True, False, True, False]) == 0.0


def test_thresholds_come_from_the_dev_half_alone():
    rows = []
    for t in range(30):                          # every turn's scores differ, so dev and test
        rows.append({"turn": f"t{t}", "cosine": t / 30, "p": 1 - t / 30, "constrains": True})
        rows.append({"turn": f"t{t}", "cosine": 0.0, "p": 0.0, "constrains": False})
    dev, test = score.split([r["turn"] for r in rows], seed=5)
    d = [r for r in rows if r["turn"] in dev]
    want_e = score.threshold([r["cosine"] for r in d], [r["constrains"] for r in d])
    want_j = score.threshold([r["p"] for r in d], [r["constrains"] for r in d])
    t = [r for r in rows if r["turn"] in test]
    on_test = score.threshold([r["cosine"] for r in t], [r["constrains"] for r in t])
    assert want_e != on_test                     # the fixture can tell dev from test
    r = score.evaluate(rows, seed=5)
    assert (r["t_e"], r["t_j"]) == (want_e, want_j)


def test_evaluate_counts_false_injections_on_the_held_out_half_only():
    rows = []
    for t in range(20):
        rows.append({"turn": f"t{t}", "cosine": 0.6, "p": 0.9, "constrains": True})
        rows.append({"turn": f"t{t}", "cosine": 0.6, "p": 0.1, "constrains": False})
    r = score.evaluate(rows, seed=1)
    assert r["n_dev"] == r["n_test"] == 20
    assert r["false_inject"]["E"] == 10 and r["false_inject"]["J"] == 0
    assert r["false_inject"]["mcnemar"]["p"] == pytest.approx(2 / 1024)
    assert r["kept"]["E"] == r["kept"]["J"] == 10 and r["base_rate_test"] == 0.5
