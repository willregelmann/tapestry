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


def gw(answers, model="typesafe-ai/jev-1.13.0", slug="typesafe-ai/jev-1.13.0", provider="typesafe-ai",
       attempts=1, **extra):
    """A response shaped like the Vercel gateway's TypeSafe surface."""
    routing = {"originalModelId": model, "canonicalSlug": slug, "finalProvider": provider,
               "modelAttempts": [{"model": slug}] * attempts}
    return {"model": model, "answers": answers, "usage": {"input_tokens": 1000},
            "provider_metadata": {"gateway": {"routing": routing, "cost": "0.000042"}}, **extra}


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
    assert dropped == {"truncated": 1, "own_alarm_or_compaction": 3, "no_items": 0, "no_reply": 1,
                       "duplicate_turn": 0, "no_cosine": 0}


def test_the_labeller_sheet_never_shows_cosine_or_match(tmp_path):
    rows = [(1, "s", "user", "Please review #108", "Please review #108\n" + BLOCK),
            (2, "s", "assistant", "Done.", None)]
    m = corpus.build(_db(tmp_path, rows), tmp_path / "out", n_turns=5, seed=1)
    assert m["candidates"] == 3 and m["turns_sampled"] == 1
    sheet = [json.loads(x) for x in (tmp_path / "out" / "sheet.jsonl").read_text().splitlines()]
    assert set(sheet[0]) == {"item", "message", "memory", "reply", "label"}
    assert m["labels"] == ["constrains", "informs", "irrelevant"]
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
    assert body["model"] == "typesafe-ai/jev-1.13.0"
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
        return gw({"c0": {"type": "noul", "noul": 0.9}, "c1": {"type": "noul", "noul": 0.2}})

    got = jev.Jev(key, send=fake, session_id="gate-run-1").gate("m", [("a", "x"), ("b", "y")])
    assert got["p"] == {"a": 0.9, "b": 0.2}
    assert got["model"] == "typesafe-ai/jev-1.13.0|typesafe-ai/jev-1.13.0|typesafe-ai"
    assert got["cost"] == "0.000042"
    assert len(sent) == 1 and len(sent[0]["questions"]) == 2
    assert sent[0]["session_id"] == "gate-run-1"
    assert sent[0]["providerOptions"] == {"gateway": {"zeroDataRetention": True}}


def test_what_answered_comes_from_routing_and_must_be_typesafe_alone(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test")
    ans = {"c0": {"type": "noul", "noul": 0.5}}
    no_routing = {"model": "typesafe-ai/jev-1.13.0", "answers": ans}
    with pytest.raises(RuntimeError, match="no model or routing slug"):
        jev.Jev(key, send=lambda b: no_routing).gate("m", [("a", "x")])
    with pytest.raises(RuntimeError, match="not TypeSafe"):
        jev.Jev(key, send=lambda b: gw(ans, provider="openai")).gate("m", [("a", "x")])
    with pytest.raises(RuntimeError, match="fallback"):
        jev.Jev(key, send=lambda b: gw(ans, attempts=2)).gate("m", [("a", "x")])


def test_a_build_change_mid_run_stops_the_run(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test")
    slugs = iter(["typesafe-ai/jev-1.13.0", "typesafe-ai/jev-1.13.0", "typesafe-ai/jev-1.14.0"])
    ans = {"c0": {"type": "noul", "noul": 0.5}}
    j = jev.Jev(key, send=lambda b: gw(ans, slug=next(slugs)))
    j.gate("m", [("a", "x")])
    j.gate("m", [("a", "x")])
    with pytest.raises(jev.BuildChanged, match="1.13.0.* -> .*1.14.0"):
        j.gate("m", [("a", "x")])
    with pytest.raises(RuntimeError, match="no model or routing slug"):
        jev.Jev(key, send=lambda b: {"answers": ans}).gate("m", [("a", "x")])


def test_a_missing_answer_is_an_error_never_a_default(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test")
    resp = gw({"c0": {"type": "noul", "noul": 0.5}})
    with pytest.raises(RuntimeError, match="no answer for b"):
        jev.Jev(key, send=lambda b: resp).gate("m", [("a", "x"), ("b", "y")])


def test_a_malformed_answer_is_an_error_never_a_probability(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test")
    bad = gw({"c0": {"type": "choice", "choice": "yes"}})
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
    same = score.agreement(["constrains", "informs", "irrelevant"], ["constrains", "informs", "irrelevant"])
    assert same["kappa_3way"] == 1.0 and same["percent_3way"] == 1.0
    a = ["constrains", "constrains", "informs", "irrelevant"]
    b = ["constrains", "informs", "constrains", "irrelevant"]
    g = score.agreement(a, b)
    assert g["percent_3way"] == 0.5 and g["percent_binary"] == 0.5
    assert g["kappa_binary"] == 0.0                     # 2x2 at chance
    assert g["kappa_3way"] == pytest.approx((0.5 - 0.375) / 0.625)


def test_thresholds_come_from_the_dev_half_alone():
    rows = []
    for t in range(30):                          # every turn's scores differ, so dev and test
        rows.append({"turn": f"t{t}", "cosine": t / 30, "p": 1 - t / 30, "label": "constrains"})
        rows.append({"turn": f"t{t}", "cosine": 0.0, "p": 0.0, "label": "irrelevant"})
    dev, test = score.split([r["turn"] for r in rows], seed=5)
    d = [r for r in rows if r["turn"] in dev]
    yd = [r["label"] == "constrains" for r in d]
    want_e = score.threshold([r["cosine"] for r in d], yd)
    want_j = score.threshold([r["p"] for r in d], yd)
    t = [r for r in rows if r["turn"] in test]
    on_test = score.threshold([r["cosine"] for r in t], [r["label"] == "constrains" for r in t])
    assert want_e != on_test                     # the fixture can tell dev from test
    r = score.evaluate(rows, seed=5)
    assert (r["t_e"], r["t_j"]) == (want_e, want_j)


def test_evaluate_primary_is_paired_false_injection_among_non_constraining_items():
    rows = []
    for t in range(20):
        rows.append({"turn": f"t{t}", "cosine": 0.6, "p": 0.9, "label": "constrains"})
        rows.append({"turn": f"t{t}", "cosine": 0.6, "p": 0.1,
                     "label": "informs" if t % 2 else "irrelevant"})
    r = score.evaluate(rows, seed=1)
    assert r["n_dev"] == r["n_test"] == 20
    fi = r["false_inject"]
    assert (fi["E"], fi["J"], fi["of"]) == (10, 0, 10)       # informs AND irrelevant count
    assert (fi["mcnemar"]["only_a"], fi["mcnemar"]["only_b"]) == (10, 0)
    assert fi["mcnemar"]["p"] == pytest.approx(2 / 1024)
    assert r["recall_test"]["E"] == r["recall_test"]["J"] == 1.0 and r["base_rate_test"] == 0.5
    assert r["informs_dropped"]["E"] == 0.0 and r["informs_dropped"]["J"] == 1.0
    assert r["informs_dropped"]["of"] == r["labels_test"]["informs"] > 0
    assert r["descriptive_share_not_constraining_of_injected"] == {"E": 0.5, "J": 0.0}


def test_a_score_equal_to_the_threshold_is_injected():
    rows = []
    for t in range(20):
        rows.append({"turn": f"t{t}", "cosine": 0.5, "p": 0.5, "label": "constrains"})
        rows.append({"turn": f"t{t}", "cosine": 0.5, "p": 0.5, "label": "irrelevant"})
    r = score.evaluate(rows, seed=2)
    assert r["t_e"] == 0.5 and r["recall_test"]["E"] == 1.0 and r["false_inject"]["E"] == 10


def test_an_unlabelled_row_stops_scoring():
    rows = [{"turn": "t0", "cosine": 0.5, "p": 0.5, "label": None}]
    with pytest.raises(ValueError, match="unlabelled"):
        score.evaluate(rows, seed=1)


def test_credential_shaped_strings_are_redacted_before_anything_is_written(tmp_path):
    tok = "sk-or-v1-" + "a1B2" * 12
    gh = "ghp_" + "Z9" * 18
    blk = BLOCK.replace("Buster runs on Sonnet.", f"Key was {tok} once.")
    rows = [(1, "s", "user", f"use token: {'q' * 24} and {gh}", f"x\n" + blk),
            (2, "s", "assistant", f"Authorization: Bearer {'x' * 30}", None)]
    m = corpus.build(_db(tmp_path, rows), tmp_path / "out", n_turns=5, seed=1)
    out = (tmp_path / "out" / "corpus.jsonl").read_text() + (tmp_path / "out" / "sheet.jsonl").read_text()
    for secret in (tok, gh, "q" * 24, "x" * 30):
        assert secret not in out
    assert "[REDACTED]" in out and m["redactions"] >= 4
    assert "Merge with --match-head-commit." in out          # ordinary text survives


def test_ordinary_words_ending_in_sk_or_gh_are_not_redacted():
    for text in ("/home/foil/work/reviews/frontdesk-claim-own-seat-body.md",
                 "the highest_threshold_value_here", "risk-assessment-for-the-quarter-2026"):
        assert corpus.redact(text) == (text, 0)


def _corpus(tmp_path, turns=4, per=2):
    p = tmp_path / "corpus.jsonl"
    with open(p, "w") as f:
        for t in range(turns):
            for c in range(per):
                f.write(json.dumps({"item": f"t{t}:{c}", "turn": f"t{t}", "message": f"m{t}",
                                    "memory": f"mem{t}.{c}", "cosine": 0.5, "match": "ok", "rank": c + 1}) + "\n")
    key = tmp_path / "key"
    key.write_text("sk-test")
    return p, key


def _answer(body, model="typesafe-ai/jev-1.13.0"):
    return gw({k: {"type": "noul", "noul": 0.5} for k in body["questions"]}, slug=model)
    return {"model": model, "usage": {"input_tokens": 1000},
            "answers": {k: {"type": "noul", "noul": 0.5} for k in body["questions"]}}


def test_a_resumed_run_repeats_no_paid_call(tmp_path):
    from bench.gate import run
    corpus, key = _corpus(tmp_path)
    calls = []

    def flaky(body):
        calls.append(body["state"]["message"])
        if body["state"]["message"] == "m2":
            raise RuntimeError("Jev HTTP 400: bad")        # not retryable: the run stops here
        return _answer(body)

    ans = tmp_path / "answers.jsonl"
    with pytest.raises(RuntimeError, match="400"):
        run.run(corpus, ans, jev.Jev(key, send=flaky), frozenset())
    assert [json.loads(l)["turn"] for l in ans.read_text().splitlines()] == ["t0", "t1"]
    calls.clear()
    got = run.run(corpus, ans, jev.Jev(key, send=_answer_rec(calls)), frozenset())
    assert calls == ["m2", "m3"] and got["answered_now"] == 2
    assert [json.loads(l)["turn"] for l in ans.read_text().splitlines()] == ["t0", "t1", "t2", "t3"]


def _answer_rec(calls, model="typesafe-ai/jev-1.13.0"):
    def f(body):
        calls.append(body["state"]["message"])
        return _answer(body, model)
    return f


def test_a_resumed_run_stops_if_the_build_changed_since_the_first_answers(tmp_path):
    from bench.gate import run
    corpus, key = _corpus(tmp_path)
    ans = tmp_path / "answers.jsonl"
    seen = []
    first = jev.Jev(key, send=lambda b: (seen.append(1), _answer(b))[1] if len(seen) < 2 else (_ for _ in ()).throw(RuntimeError("Jev HTTP 400")))
    with pytest.raises(RuntimeError):
        run.run(corpus, ans, first, frozenset())
    with pytest.raises(jev.BuildChanged):
        run.run(corpus, ans, jev.Jev(key, send=_answer_rec([], "typesafe-ai/jev-1.14.0")), frozenset())


def test_retryable_failures_are_retried_with_backoff_and_others_are_not(tmp_path):
    from bench.gate import run
    corpus, key = _corpus(tmp_path, turns=1)
    n = []

    def busy(body):
        n.append(1)
        if len(n) < 3:
            raise jev.Retryable("Jev HTTP 429")
        return _answer(body)

    waits = []
    got = run.run(corpus, tmp_path / "a.jsonl", jev.Jev(key, send=busy), frozenset(), sleep=waits.append)
    assert len(n) == 3 and waits == [1, 2] and got["answered_now"] == 1
    with pytest.raises(jev.Retryable):
        run.run(corpus, tmp_path / "b.jsonl",
                jev.Jev(key, send=lambda b: (_ for _ in ()).throw(jev.Retryable("529"))), frozenset(),
                tries=3, sleep=lambda s: None)


def test_the_spend_cap_stops_before_the_next_call(tmp_path):
    from bench.gate import run
    corpus, key = _corpus(tmp_path, turns=5)
    calls = []
    got = run.run(corpus, tmp_path / "a.jsonl", jev.Jev(key, send=_answer_rec(calls)), frozenset(),
                  cap_usd=2 * 1000 * run.PRICE_PER_TOKEN)
    assert got["stopped"] == "spend cap" and len(calls) == 2


def test_http_status_decides_retry_and_the_key_never_appears_in_an_error(tmp_path, monkeypatch):
    import http.server
    import threading
    codes = iter([429, 529, 503, 400, 401])

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            code = next(codes)
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b'{"error":"nope"}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(jev, "ENDPOINT", f"http://127.0.0.1:{srv.server_port}/")
        key = tmp_path / "key"
        key.write_text("sk-SECRET-VALUE-0123456789")
        j = jev.Jev(key, timeout=5)
        for want in (jev.Retryable, jev.Retryable, jev.Retryable):
            with pytest.raises(want) as e:
                j.gate("m", [("a", "x")])
            assert "SECRET" not in str(e.value)
        for _ in range(2):
            with pytest.raises(RuntimeError) as e:
                j.gate("m", [("a", "x")])
            assert not isinstance(e.value, jev.Retryable) and "SECRET" not in str(e.value)
    finally:
        srv.shutdown()


# --- Wren's review of 3099edf6: duplicates, no-cosine, verdict, matched-recall, inject-all ---

def test_a_doubled_turn_is_one_turn_and_the_drop_is_counted(tmp_path):
    rows = [(1, "s", "user", "Please review #108", "Please review #108\n" + BLOCK),
            (2, "s", "assistant", "Done.", None),
            (3, "s", "user", "Please review #108", "Please review #108\n" + BLOCK),   # Hermes double-write
            (4, "s", "assistant", "Done again.", None),
            (5, "s", "user", "Please review #108", "Please review #108\nno recall")]
    got, dropped = corpus.turns(sqlite3.connect(_db(tmp_path, rows)))
    assert [t["turn"] for t in got] == ["s:1"] and dropped["duplicate_turn"] == 1
    m = corpus.build(tmp_path / "state.db", tmp_path / "out", n_turns=5, seed=1)   # _db wrote it
    assert m["dropped"]["duplicate_turn"] == 1 and m["turns_eligible"] == 1


def test_the_same_message_in_two_sessions_is_two_turns(tmp_path):
    rows = [(1, "s", "user", "hi", "hi\n" + BLOCK), (2, "s", "assistant", "a", None),
            (3, "t", "user", "hi", "hi\n" + BLOCK), (4, "t", "assistant", "b", None)]
    got, dropped = corpus.turns(sqlite3.connect(_db(tmp_path, rows)))
    assert len(got) == 2 and dropped["duplicate_turn"] == 0


def test_a_candidate_with_no_cosine_is_dropped_and_counted_at_build(tmp_path):
    block = "<memory-context>\n[#5 · match: ok · learned Oct 2026] no cos here\n" \
            "[#6 · match: ok · cos 0.40 · learned Oct 2026] has cos\n</memory-context>"
    rows = [(1, "s", "user", "q", "q\n" + block), (2, "s", "assistant", "r", None)]
    got, dropped = corpus.turns(sqlite3.connect(_db(tmp_path, rows)))
    assert [c["memory"] for c in got[0]["candidates"]] == [6] and dropped["no_cosine"] == 1


def _result(e_fi, j_fi, p, e_rc, j_rc, of=10, e_drop=(), j_drop=()):
    return {"false_inject": {"E": e_fi, "J": j_fi, "mcnemar": {"p": p}},
            "recall_test": {"E": e_rc, "J": j_rc, "of": of},
            "constrains_dropped": {"E": list(e_drop), "J": list(j_drop)}}


PASSING = dict(e_fi=20, j_fi=5, p=0.001, e_rc=0.9, j_rc=0.9)


def test_verdict_passes_only_when_all_three_hold():
    assert score.verdict(_result(**PASSING), [0.2] * 40)["verdict"] == "PASS"


@pytest.mark.parametrize("change,lat,why", [
    (dict(e_fi=0, j_fi=20, p=1.9e-6), 0.2, "false injection"),     # J worse, significantly
    (dict(p=0.2), 0.2, "false injection"),                          # J better, not significantly
    (dict(j_fi=20), 0.2, "false injection"),                        # tie
    (dict(j_rc=0.84), 0.2, "recall"),                               # 6 points below E
    ({}, 1.0, "latency"),                                           # p95 at the limit
])
def test_verdict_is_null_when_any_condition_fails(change, lat, why):
    v = score.verdict(_result(**{**PASSING, **change}), [0.2] * 37 + [lat] * 3)   # 3/40 > 5%: sets p95
    assert v["verdict"] == "NULL" and any(w.startswith(why) for w in v["why"])


def test_verdict_edges_that_pass():
    assert score.verdict(_result(**{**PASSING, "j_rc": 0.85}), [0.2])["verdict"] == "PASS"  # gap = 5
    assert score.verdict(_result(**PASSING), [0.99] * 20)["verdict"] == "PASS"


def test_verdict_with_no_test_positives_is_null():
    assert score.verdict(_result(**PASSING, of=0), [0.2])["verdict"] == "NULL"


def test_p95_is_nearest_rank():
    assert score.p95(list(range(1, 101))) == 95 and score.p95([3.0]) == 3.0
    assert score.p95([1.0] * 19 + [9.0]) == 1.0 and score.p95([1.0] * 18 + [9.0] * 2) == 9.0


def test_e_moved_to_js_test_recall_and_the_inject_all_row():
    rows = []
    for t in range(20):
        rows.append({"turn": f"t{t}", "cosine": 0.3 + t / 100, "p": 0.9 if t % 2 else 0.95,
                     "label": "constrains"})
        rows.append({"turn": f"t{t}", "cosine": 0.35 + t / 100, "p": 0.1, "label": "irrelevant"})
    r = score.evaluate(rows, seed=3)
    j_kept = round(r["recall_test"]["J"] * r["recall_test"]["of"])
    d = r["descriptive_E_at_J_recall"]
    assert d["kept"] == j_kept
    test_pos = sorted((x["cosine"] for x in rows if x["label"] == "constrains"
                       and x["turn"] in score.split([q["turn"] for q in rows], 3)[1]), reverse=True)
    assert d["threshold"] == test_pos[j_kept - 1]
    assert r["descriptive_inject_all"] == {"false_inject": r["false_inject"]["of"], "recall": 1.0}


def test_an_excluded_turn_never_reaches_the_split():
    rows = []
    for t in range(20):
        rows.append({"turn": f"t{t}", "cosine": 0.6, "p": 0.9, "label": "constrains"})
        rows.append({"turn": f"t{t}", "cosine": 0.6, "p": 0.1, "label": "irrelevant"})
    rows.append({"turn": "dup", "cosine": 0.99, "p": 0.99, "label": "irrelevant"})
    full = score.evaluate(rows, seed=1)
    cut = score.evaluate(rows, seed=1, exclude=frozenset({"dup"}))
    assert full["n_dev"] + full["n_test"] == 41 and cut["n_dev"] + cut["n_test"] == 40
    assert cut == score.evaluate(rows[:-1], seed=1)


# --- rev 5: a dropped constraint is never a bare PASS ---

def test_a_pass_where_j_alone_drops_a_constraint_says_so_and_names_it():
    v = score.verdict(_result(**PASSING, e_drop=["a:1:7"], j_drop=["a:1:7", "a:2:9"]), [0.2] * 40)
    assert v["verdict"] == "PASS, with a constraint dropped"
    assert v["dropped_by_J_only"] == ["a:2:9"]                  # a:1:7 is dropped by both: not J's doing


def test_a_pass_where_both_drop_the_same_constraint_is_a_plain_pass():
    v = score.verdict(_result(**PASSING, e_drop=["a:1:7"], j_drop=["a:1:7"]), [0.2] * 40)
    assert v["verdict"] == "PASS" and v["dropped_by_J_only"] == []


def test_a_null_still_lists_what_was_dropped():
    v = score.verdict(_result(**{**PASSING, "p": 0.5}, j_drop=["a:2:9"]), [0.2] * 40)
    assert v["verdict"] == "NULL" and v["constrains_dropped"]["J"] == ["a:2:9"]


def test_evaluate_names_the_test_constrains_each_arm_drops():
    rows = []
    for t in range(80):
        low = t % 20 == 19                     # one turn in twenty (under the 10% the threshold may give up): J scores its constraint near zero
        for k in range(3):
            pos = k == 0
            rows.append({"turn": f"t{t}", "item": f"t{t}:{k}", "label": "constrains" if pos else "informs",
                         "cosine": 0.9 if pos else 0.1,          # E ranks every constraint first
                         "p": (0.0 if low else 0.9) if pos else 0.5})
    r = score.evaluate(rows, seed=1)
    dropped = r["constrains_dropped"]
    assert dropped["E"] == []
    assert dropped["J"], "the fixture must make J drop something, or this test proves nothing"
    assert all(i.endswith(":0") and int(i.split(":")[0][1:]) % 20 == 19 for i in dropped["J"])
    assert r["recall_test"]["of"] - len(dropped["J"]) == round(r["recall_test"]["J"] * r["recall_test"]["of"])


def test_an_excluded_turn_is_never_sent_to_jev(tmp_path):
    from bench.gate import run
    corpus, key = _corpus(tmp_path)               # turns t0..t3 with messages m0..m3
    ex = tmp_path / "exclude.json"
    ex.write_text(json.dumps({"turns": {"t1": "money", "t3": "figure"}}))
    sent = []
    got = run.run(corpus, tmp_path / "a.jsonl", jev.Jev(key, send=_answer_rec(sent)), run.excluded(ex))
    assert sent == ["m0", "m2"] and got["answered_now"] == 2
    assert [json.loads(l)["turn"] for l in (tmp_path / "a.jsonl").read_text().splitlines()] == ["t0", "t2"]


def test_the_cli_refuses_to_run_without_the_exclusion_list(tmp_path, monkeypatch):
    from bench.gate import run
    monkeypatch.setattr("sys.argv", ["run", "c.jsonl", "a.jsonl", "--key-file", "k"])
    with pytest.raises(SystemExit) as e:
        run.main()
    assert e.value.code == 2                       # argparse: a required argument is missing
