"""Run Jev over the frozen corpus, one request per turn, resumable.

    python -m bench.gate.run CORPUS_JSONL ANSWERS_JSONL --exclude EXCLUDE_JSON --key-file KEY [--session-id ID]

Each turn's answer is appended to ANSWERS_JSONL (and flushed to disk) as soon as it returns, so a
re-run skips every turn already answered: no paid call is repeated. The build that answered is
read back from the file on a re-run, so a resumed run still stops if the build changed. 429, 529
and 5xx are retried with backoff; anything else stops the run. Spend is capped (default $5) from the
input tokens each response reports.

Every turn named in the frozen exclude.json (duplicates, money-agent turns, figure hits) is skipped
before any request, so it never leaves the machine. The argument is required so a run can't omit it.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import OrderedDict
from pathlib import Path

from bench.gate import jev

PRICE_PER_TOKEN = 0.042 / 1_000_000      # input; output is free
Retryable = jev.Retryable


def by_turn(corpus: Path) -> "OrderedDict[str, dict]":
    turns: OrderedDict[str, dict] = OrderedDict()
    for line in corpus.read_text().splitlines():
        r = json.loads(line)
        t = turns.setdefault(r["turn"], {"message": r["message"], "candidates": []})
        t["candidates"].append((r["item"], r["memory"]))
    return turns


def done(answers: Path) -> tuple[set[str], str | None, float]:
    """Turns already answered, the build that answered them, and the spend so far."""
    seen, build, spent = set(), None, 0.0
    if answers.exists():
        for line in answers.read_text().splitlines():
            r = json.loads(line)
            seen.add(r["turn"])
            build = build or r["model"]
            spent += (r.get("usage") or {}).get("input_tokens", 0) * PRICE_PER_TOKEN
    return seen, build, spent


def excluded(path: Path) -> frozenset:
    return frozenset(json.loads(path.read_text())["turns"])


def run(corpus: Path, answers: Path, client: jev.Jev, exclude: frozenset, cap_usd: float = 5.0,
        tries: int = 5, sleep=time.sleep) -> dict:
    seen, build, spent = done(answers)
    client.build = build                      # a resumed run must match the first build
    n = 0
    with open(answers, "a") as out:
        for turn, t in by_turn(corpus).items():
            if turn in seen or turn in exclude:
                continue
            if spent >= cap_usd:
                return {"stopped": "spend cap", "spent_usd": spent, "answered_now": n}
            got = None
            for attempt in range(tries):
                try:
                    got = client.gate(t["message"], t["candidates"])
                    break
                except Retryable:
                    if attempt == tries - 1:
                        raise
                    sleep(2 ** attempt)
            assert got is not None
            out.write(json.dumps({"turn": turn, **got}) + "\n")
            out.flush()
            os.fsync(out.fileno())
            spent += (got.get("usage") or {}).get("input_tokens", 0) * PRICE_PER_TOKEN
            n += 1
    return {"stopped": None, "spent_usd": spent, "answered_now": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("answers", type=Path)
    ap.add_argument("--exclude", type=Path, required=True)
    ap.add_argument("--key-file", type=Path, required=True)
    ap.add_argument("--session-id")
    ap.add_argument("--cap-usd", type=float, default=5.0)
    a = ap.parse_args()
    print(json.dumps(run(a.corpus, a.answers, jev.Jev(a.key_file, session_id=a.session_id),
              excluded(a.exclude), a.cap_usd)))


if __name__ == "__main__":
    main()
