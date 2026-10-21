"""Build the gate corpus from LongMemEval instead of a Hermes state.db.

    python -m bench.gate.lme_corpus OUT_DIR --per-type 15 --seed 20261018

LongMemEval (Wu et al., arXiv 2410.10813) is public, and its authors marked which user turns hold
the evidence for each question (`has_answer`). So this corpus needs no consent and no labellers.
Each question gets its own Mind. Every user turn of its haystack is remembered the way a host files
a turn (`filing.turn_namespaces`: its session's namespace plus a shared place), and the question is
recalled with `Mind.recall`, so the candidates are exactly what Tapestry's hybrid recall (cosine and
keyword, current memories, visible namespaces) would have injected, up to MAX_PER_TURN. A candidate
is relevant exactly when the dataset marks its turn as evidence. Writes:

  OUT_DIR/corpus.jsonl  one candidate per line, the same fields corpus.py writes (the scorer's input)
  OUT_DIR/labels.json   item -> {"label", "stale"}, in the shape score.merge produces
  OUT_DIR/manifest.json the seed, the counts, and the sha256 of the inputs and outputs

What this measures differs from the Hermes corpus, and the differences matter:
- The label is "this turn contains the answer to the question", the dataset's, not "this memory
  bears on this message". Evidence turns are "informs"; no label is "constrains" (the constrains
  rows read n=0) and none is stale. A distractor that is on-topic but isn't evidence is labelled
  irrelevant, so a Jev that rightly calls it relevant is charged a false injection. That biases
  the test against J: a PASS is conservative, a NULL is ambiguous.
- Only user turns are memories, so the assistant-evidence question type is skipped.
- Nothing is superseded and the namespace filter hides nothing, so the stale and out-of-scope
  cases real recall has already removed are absent here too.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import tempfile
from collections import defaultdict
from pathlib import Path

from bench import longmemeval
from bench.longmemeval import DEFAULT_PATH
from tapestry import filing
from tapestry.mind import Mind

MAX_PER_TURN = 5                      # corpus.MAX_PER_TURN: what recall injects per turn
PLACE = "haystack"                    # the place every session of one haystack happened in
# The evidence for these is in the assistant's reply, and Tapestry's memories are what the user
# said, so no candidate could ever be relevant. Left out, and named in the manifest.
SKIPPED_KINDS = frozenset({"single-session-assistant"})


def kind(item: dict) -> str:
    return "abstention" if item["question_id"].endswith("_abs") else item["question_type"]


def sample(items: list[dict], per_type: int, seed: int) -> list[dict]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        if kind(it) not in SKIPPED_KINDS:
            by_type[kind(it)].append(it)
    rng = random.Random(seed)
    chosen: list[dict] = []
    for t in sorted(by_type):
        group = sorted(by_type[t], key=lambda it: it["question_id"])
        chosen += rng.sample(group, min(per_type, len(group)))
    return sorted(chosen, key=lambda it: it["question_id"])


def load(item: dict, mind: Mind) -> dict[int, dict]:
    """Remember every user turn of the haystack, one memory each. Returns memory id -> {"turn",
    "evidence"}. Abstention questions have no evidence, whatever the turns say."""
    negative = item["question_id"].endswith("_abs")
    held: dict[int, dict] = {}
    seen: set[str] = set()
    for sid, when, session in zip(item["haystack_session_ids"], item["haystack_dates"],
                                  item["haystack_sessions"]):
        if sid in seen:                    # a few haystacks repeat a session; keep the first
            continue
        seen.add(sid)
        at = longmemeval._date(when).timestamp()
        for i, turn in enumerate(session):
            text = turn["content"].strip()
            if turn["role"] != "user" or not text:
                continue
            mid = mind.remember(text, source="user", at=at, key=f"{sid}#{i}",
                                namespaces=filing.turn_namespaces(sid, PLACE))
            held[mid] = {"turn": f"{sid}#{i}",
                         "evidence": bool(turn.get("has_answer")) and not negative}
    return held


def build(data: Path, out: Path, per_type: int, seed: int, encode=None, model_tag: str | None = None) -> dict:
    if encode is None:
        from bench.systems.embed_cache import EmbedCache
        from tapestry import embed
        enc, cache = embed.Encoder(), EmbedCache(embed.MODEL_TAG)
        encode, model_tag = (lambda texts: cache.encode(texts, enc)), embed.MODEL_TAG   # noqa: E731
    raw = data.read_bytes()
    chosen = sample(json.loads(raw), per_type, seed)
    out.mkdir(parents=True, exist_ok=True)
    labels: dict[str, dict] = {}
    n = n_pos = n_no_evidence = n_short = 0
    by_kind: dict[str, int] = defaultdict(int)
    with tempfile.TemporaryDirectory() as tmp, open(out / "corpus.jsonl", "w") as corpus:
        for it in chosen:
            mind = Mind(Path(tmp) / f"{it['question_id']}.db", encode=encode, model_tag=model_tag)
            try:
                held = load(it, mind)
                got = mind.recall(it["question"], namespaces=[PLACE], k=MAX_PER_TURN)
            finally:
                mind.close()
            by_kind[kind(it)] += 1
            n_no_evidence += not any(held[r.id]["evidence"] for r in got)
            n_short += len(got) < MAX_PER_TURN
            for rank, r in enumerate(got, 1):
                item = f"{it['question_id']}:{held[r.id]['turn']}"
                corpus.write(json.dumps({"item": item, "turn": it["question_id"],
                                         "message": it["question"], "memory": r.content,
                                         "cosine": r.cosine, "match": r.match, "rank": rank}) + "\n")
                labels[item] = {"label": "informs" if held[r.id]["evidence"] else "irrelevant",
                                "stale": False}
                n += 1
                n_pos += held[r.id]["evidence"]
    (out / "labels.json").write_text(json.dumps(labels, indent=1, sort_keys=True) + "\n")
    manifest = {"source": "LongMemEval cleaned S", "source_sha256": hashlib.sha256(raw).hexdigest(),
                "candidates_from": "Mind.recall (hybrid), namespaces=[place], k=5",
                "seed": seed, "per_type": per_type, "skipped_kinds": sorted(SKIPPED_KINDS),
                "questions": len(chosen), "by_kind": dict(sorted(by_kind.items())),
                "candidates": n, "relevant": n_pos, "questions_without_evidence_in_top": n_no_evidence,
                "questions_with_fewer_than_k": n_short,
                "corpus_sha256": hashlib.sha256((out / "corpus.jsonl").read_bytes()).hexdigest(),
                "labels_sha256": hashlib.sha256((out / "labels.json").read_bytes()).hexdigest()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--data", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--per-type", type=int, default=15)
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    print(json.dumps(build(a.data, a.out, a.per_type, a.seed), indent=1))


if __name__ == "__main__":
    main()
