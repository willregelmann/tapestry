"""LongMemEval (cleaned, S split) as harness fixtures.

LongMemEval (Wu et al., arXiv 2410.10813) gives each question its own
haystack of ~48 chat sessions, of which one or more contain the answer.
Each question becomes one fixture: the sessions are its memories and the
answer sessions are the expected hits. That's the paper's session-level
retrieval metric. Abstention questions (ids ending in "_abs") have no answer
in the haystack, so they become negatives.

The data isn't committed. Fetch it with:

    python -m bench.longmemeval --download

Fixtures are named "lme:<question_type>", so the runner reports one row per
type.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import urllib.request
from collections import defaultdict
from pathlib import Path

from bench.fixture import Fixture, Memory, Query

DATA_DIR = Path(__file__).resolve().parent / "data" / "longmemeval"
DEFAULT_PATH = DATA_DIR / "longmemeval_s_cleaned.json"
_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/"
FILES = ("longmemeval_s_cleaned.json", "longmemeval_oracle.json")


def _date(s: str) -> dt.datetime:
    # "2023/05/30 (Tue) 23:40"
    day, _, clock = s.split(" ")
    return dt.datetime.strptime(f"{day} {clock}", "%Y/%m/%d %H:%M").replace(tzinfo=dt.timezone.utc)


def _render(session: list[dict]) -> str:
    return "\n".join(f'{t["role"]}: {t["content"]}' for t in session)


def to_fixture(item: dict, granularity: str = "session") -> Fixture:
    qtype = item["question_type"]
    negative = item["question_id"].endswith("_abs")
    memories: list[Memory] = []
    expect: list[str] = []
    seen: set[str] = set()
    answer_sessions = set(item["answer_session_ids"])
    for sid, when, session in zip(item["haystack_session_ids"], item["haystack_dates"],
                                  item["haystack_sessions"]):
        if sid in seen:  # a few haystacks repeat a session; keep the first
            continue
        seen.add(sid)
        at = _date(when)
        if granularity == "session":
            memories.append(Memory(id=sid, content=_render(session), at=at))
            if sid in answer_sessions and not negative:
                expect.append(sid)
        elif granularity == "turn":
            for i, turn in enumerate(session):
                mid = f"{sid}#{i}"
                memories.append(Memory(id=mid, content=f'{turn["role"]}: {turn["content"]}', at=at))
                if turn.get("has_answer") and not negative:
                    expect.append(mid)
        else:
            raise ValueError(f"granularity must be 'session' or 'turn', not {granularity!r}")
    query = Query(text=item["question"], expect=tuple(expect), at=_date(item["question_date"]),
                  note=f'{item["question_id"]}: {item["answer"]}')
    name = "lme:abstention" if negative else f"lme:{qtype}"
    return Fixture(name=name, memories=memories, queries=[query],
                   description=item["question_id"])


def load(path: Path = DEFAULT_PATH, *, per_type: int | None = 10, seed: int = 0,
         types: set[str] | None = None, granularity: str = "session") -> list[Fixture]:
    """A stratified sample: `per_type` questions from each question type
    (abstention counts as its own type), drawn with a fixed seed so runs are
    comparable. per_type=None loads all 500."""
    items = json.loads(Path(path).read_text())
    by_type: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        t = "abstention" if it["question_id"].endswith("_abs") else it["question_type"]
        if types is None or t in types:
            by_type[t].append(it)
    rng = random.Random(seed)
    chosen: list[dict] = []
    for t in sorted(by_type):
        group = sorted(by_type[t], key=lambda it: it["question_id"])
        chosen += group if per_type is None else rng.sample(group, min(per_type, len(group)))
    return [to_fixture(it, granularity) for it in chosen]


def download(dest: Path = DATA_DIR) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = dest / name
        if target.exists():
            print(f"have {target}")
            continue
        print(f"fetching {name} ...")
        urllib.request.urlretrieve(_URL + name, target)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch or inspect LongMemEval.")
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args()
    if args.download:
        download()
    else:
        fxs = load()
        print(f"{len(fxs)} fixtures, {sum(len(f.memories) for f in fxs)} memories")
