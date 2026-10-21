"""Does the conflict judge flag the pair a correction makes, and little else? LongMemEval knowledge-update.

    python -m bench.gate.lme_conflicts OUT_DIR --n 25 --seed 20261019 --key-file KEY [--offset N]
    python -m bench.gate.lme_conflicts OUT_DIR --n 25 --seed 20261019 --dry-run

This runs the production path, not a copy of it: each question gets a Mind, its user turns are
remembered in date order as lasting memories (filed in the shared place only, because the job
doesn't compare turns filed under `session.*`), and after each session's turns `jobs.judge_pending`
runs with `tapestry.judge.JevJudge`, the same prompt (`conflicts_v1`) and categories the job uses.

A knowledge-update question's haystack holds an old value and, later, a new one (`has_answer` marks
both). So the dataset names the pairs that are in conflict: each old evidence turn against the newest.
Every other pair the judge compared is unlabelled, and some of those may truly conflict:

  reachable   a true pair the judge was shown at all (the job compares a memory with its 10 nearest
              neighbours at cosine 0.30 or better, so a true pair outside that is never judged)
  flagged     the judge's probability for a pair reached a threshold
  recall      flagged / reachable, over true pairs
  collateral  flagged pairs that aren't true pairs and aren't two evidence turns. The dataset can't
              call them right or wrong: a sample goes to sheet.jsonl for a person to label, and until
              that is done "precision" isn't claimed

Thresholds 0.5, 0.7 and 0.9 are all reported. 0.7 is what the job uses (`judge.CONFLICT_P`).
Nothing here is a pass or a fail: it says how often the true pair is found and how much else is flagged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from pathlib import Path

from bench.gate.lme_corpus import PLACE
from bench.gate.lme_update import MAX_SPEND_USD, SHEET, eligible, sample, turns
from bench.longmemeval import DEFAULT_PATH
from tapestry import jobs
from tapestry import judge as judges
from tapestry.mind import Mind

THRESHOLDS = (0.5, 0.7, 0.9)


class Measured(judges.JevJudge):
    """The production judge, with the probabilities it was given kept, answers cached by request so
    no paid call is repeated, and a spend cap. `calls` counts requests that were actually sent."""

    def __init__(self, key_file: Path, cache: Path, cap_usd: float = MAX_SPEND_USD, send=None) -> None:
        super().__init__(key_file, send=self._cached)
        self._real = send or self._http
        self._cache_path, self.cap, self.calls, self.cost = cache, cap_usd, 0, 0.0
        self._cache: dict[str, dict] = {}
        if cache.exists():
            for line in cache.read_text().splitlines():
                r = json.loads(line)
                self._cache[r["k"]] = r["resp"]
        self.log: list[dict] = []
        self._last: dict = {}

    def _cached(self, body: dict) -> dict:
        k = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if k not in self._cache:
            if self.cost >= self.cap:
                raise RuntimeError(f"spend cap ${self.cap} reached; rerun to resume from the cache")
            resp = self._real(body)
            self.calls += 1
            self.cost += float((((resp.get("provider_metadata") or {}).get("gateway") or {}).get("cost")) or 0)
            self._cache[k] = resp
            with open(self._cache_path, "a") as f:
                f.write(json.dumps({"k": k, "resp": resp}) + "\n")
        self._last = self._cache[k]
        return self._last

    def conflicts(self, statement, others):
        out = super().conflicts(statement, others)
        ps = [float(self._last["answers"][f"c:{i}"]["noul"]) for i in range(len(others))]
        self.log.append({"statement": statement, "others": [(mid, p) for (mid, _), p in zip(others, ps)]})
        return out


class Counting(judges.JevJudge):
    """Stands in for the judge to count what a run would ask, without a key or a request."""

    def __init__(self) -> None:
        self.log: list[dict] = []
        self.calls = self.questions = 0

    def conflicts(self, statement, others):
        self.calls += 1
        self.questions += len(others)
        self.log.append({"statement": statement, "others": [(mid, 0.0) for mid, _ in others]})
        return {mid: "no conflict" for mid, _ in others}


def run_question(item: dict, encode, model_tag: str, judge, tmp: Path) -> dict:
    ts = turns(item)
    ev = [t for t in ts if t["evidence"]]
    new, olds = ev[-1], ev[:-1]
    true = {frozenset((o["key"], new["key"])) for o in olds}
    evidence_keys = {t["key"] for t in ev}
    mind = Mind(tmp / f"{item['question_id']}.db", encode=encode, model_tag=model_tag)
    held: dict[int, dict] = {}
    by_text: dict[str, list[int]] = {}
    pairs, used = [], set()
    try:
        sessions = list(dict.fromkeys(t["sid"] for t in ts))
        for sid in sessions:                  # a batch per session, as the job runs after a session ends
            for t in (t for t in ts if t["sid"] == sid):
                mid = mind.remember(t["text"], source="user", at=t["at"], key=t["key"], namespaces=[PLACE])
                held[mid] = t
                by_text.setdefault(t["text"], []).append(mid)
            judge.log.clear()
            jobs.judge_pending(mind, judge, limit=len(ts) + 1)
            for entry in judge.log:
                mid = next(m for m in by_text[entry["statement"]] if m not in used)
                used.add(mid)
                for other, p in entry["others"]:
                    a, b = held[mid]["key"], held[other]["key"]
                    kind = ("true" if frozenset((a, b)) in true
                            else "evidence-other" if a in evidence_keys and b in evidence_keys else "other")
                    pairs.append({"q": item["question_id"], "a": a, "b": b, "p": p, "kind": kind,
                                  "a_text": held[mid]["text"], "b_text": held[other]["text"]})
        return {"question": item["question_id"], "turns": len(ts), "true_pairs": len(true), "pairs": pairs}
    finally:
        mind.close()


def summarise(rows: list[dict]) -> dict:
    pairs = [p for r in rows for p in r["pairs"]]
    true_total = sum(r["true_pairs"] for r in rows)
    reach = [p for p in pairs if p["kind"] == "true"]
    other = [p for p in pairs if p["kind"] == "other"]
    out = {"questions": len(rows), "true_pairs": true_total, "reachable": len(reach), "other_pairs": len(other)}
    for t in THRESHOLDS:
        hit = sum(p["p"] >= t for p in reach)
        coll = sum(p["p"] >= t for p in other)
        out[f"at_{t}"] = {"true_flagged": hit, "recall_of_reachable": hit / len(reach) if reach else None,
                          "recall_of_all": hit / true_total if true_total else None,
                          "collateral": coll, "collateral_per_question": coll / len(rows) if rows else None,
                          "collateral_per_100_other_pairs": 100 * coll / len(other) if other else None,
                          "evidence_other_flagged": sum(p["p"] >= t for p in pairs if p["kind"] == "evidence-other")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--data", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--key-file", type=Path)
    ap.add_argument("--cap-usd", type=float, default=MAX_SPEND_USD)
    ap.add_argument("--dry-run", action="store_true", help="count the requests a run would make; send nothing")
    a = ap.parse_args()
    from bench.systems.embed_cache import EmbedCache
    from tapestry import embed
    enc, cache = embed.Encoder(), EmbedCache(embed.MODEL_TAG)
    items = sample(json.loads(a.data.read_bytes()), a.n, a.seed, a.offset)
    a.out.mkdir(parents=True, exist_ok=True)
    if not a.dry_run and a.key_file is None:
        ap.error("--key-file is required unless --dry-run")
    judge = Counting() if a.dry_run else Measured(a.key_file, a.out / "conflicts_v1-cache.jsonl", a.cap_usd)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for it in items:
            rows.append(run_question(it, lambda x: cache.encode(x, enc), embed.MODEL_TAG, judge, Path(tmp)))
    if a.dry_run:
        print(json.dumps({"questions": len(rows), "requests": judge.calls, "noul_questions": judge.questions}, indent=1))
        return
    (a.out / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    flagged = [p for r in rows for p in r["pairs"] if p["kind"] == "other" and p["p"] >= judges.CONFLICT_P]
    sheet = random.Random(a.seed).sample(flagged, min(SHEET, len(flagged)))
    (a.out / "sheet.jsonl").write_text("".join(json.dumps({**x, "label": None}) + "\n" for x in sheet))
    print(json.dumps({"seed": a.seed, "offset": a.offset, "prompt": judges.PROMPT, "model": judges.MODEL,
                      "sheet": len(sheet), "requests_sent": judge.calls, "cost_usd": judge.cost,
                      **summarise(rows)}, indent=1))


if __name__ == "__main__":
    main()
