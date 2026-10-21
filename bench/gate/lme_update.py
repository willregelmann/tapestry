"""Does remembering retire what a correction replaces? LongMemEval knowledge-update, end to end.

    python -m bench.gate.lme_update OUT_DIR --n 25 --seed 20261018 --decider none|oracle|embed:0.6|jev:0.5|jev2:0.5

A knowledge-update question's haystack holds an old value and, later, a new one (`has_answer` marks
both). Each question gets a Mind, and its user turns are remembered in date order, the way a host
files them. Before each turn is remembered, a decider may propose that it supersedes one of the
memories recall already holds (a proposal is made only over PROPOSE_K candidates at PROPOSE_FLOOR or better). After
the last turn the question is recalled with k=5, and the outcome is read off:

  stale_served   an older evidence turn is still in the recall (a stale serve)
  new_served     the newest evidence turn is in the recall
  new_retired    the newest evidence turn was itself superseded
  collateral     supersessions of memories that aren't an older evidence turn. The dataset says
                 nothing about these, so they can't be called right or wrong here: a sample goes to
                 sheet.jsonl for a person to label

Deciders: none (today: nothing is ever proposed), oracle (supersedes the old evidence turn when the
new one arrives, an upper bound that checks the plumbing), embed:T (the nearest candidate at cosine
>= T, no model), jev:P (the candidate with the highest `replaces` probability, if >= P), jev2:P (the same with `replaces_v2`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from pathlib import Path

from bench import longmemeval
from bench.gate import jev
from bench.gate.lme_corpus import PLACE
from bench.longmemeval import DEFAULT_PATH
from tapestry import filing
from tapestry.mind import Mind

K = 5                  # what the question's recall returns, as injected
# What a proposer is shown for each new turn: the 10 nearest current memories at cosine 0.30 or
# better. First tried at k=5 and MATCH_OK (0.45): the old evidence turn was inside that for only 14
# of 25 questions on seed 20261018's first 25, so no proposer could have reached the rest. The wider
# rule was set from those 25 and is only ever scored on a different 25 (--offset 25).
PROPOSE_K, PROPOSE_FLOOR = 10, 0.30
MAX_SPEND_USD = 3.0
SHEET = 40             # collateral supersessions sampled for a person to label (keep / wrongly retired)


def turns(item: dict) -> list[dict]:
    """The haystack's user turns in the order they happened (session date, then position). The
    haystack list isn't in date order."""
    seen: set[str] = set()
    out = []
    for n, (sid, when, session) in enumerate(zip(item["haystack_session_ids"], item["haystack_dates"],
                                                 item["haystack_sessions"])):
        if sid in seen:
            continue
        seen.add(sid)
        at = longmemeval._date(when).timestamp()
        for i, t in enumerate(session):
            text = t["content"].strip()
            if t["role"] == "user" and text:
                out.append({"key": f"{sid}#{i}", "sid": sid, "text": text, "at": at + i,
                            "evidence": bool(t.get("has_answer")), "order": (at, n, i)})
    return sorted(out, key=lambda t: t["order"])


def eligible(item: dict) -> bool:
    return (item["question_type"] == "knowledge-update" and not item["question_id"].endswith("_abs")
            and sum(t["evidence"] for t in turns(item)) >= 2)


def sample(items: list[dict], n: int, seed: int, offset: int = 0) -> list[dict]:
    pool = sorted((it for it in items if eligible(it)), key=lambda it: it["question_id"])
    order = random.Random(seed).sample(pool, len(pool))
    return sorted(order[offset:offset + n], key=lambda it: it["question_id"])


class Cached:
    """Jev's `replaces` answers, kept by (message, memory) so no paid call is repeated."""

    def __init__(self, client: jev.Jev, path: Path, noul: str = "replaces") -> None:
        self.client, self.path, self.noul, self.calls, self.cost = client, path, noul, 0, 0.0
        self._seen: dict[str, float] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                r = json.loads(line)
                self._seen[r["k"]] = r["p"]

    @staticmethod
    def _k(message: str, memory: str) -> str:
        return hashlib.sha256(f"{message}\0{memory}".encode()).hexdigest()

    def replaces(self, message: str, memories: list[str]) -> list[float]:
        miss = [m for m in memories if self._k(message, m) not in self._seen]
        if miss:
            if self.cost >= MAX_SPEND_USD:
                raise RuntimeError(f"spend cap ${MAX_SPEND_USD} reached; rerun to resume from the cache")
            got = self.client.gate(message, [(str(i), m) for i, m in enumerate(miss)], nouls=(self.noul,))
            self.calls += 1
            self.cost += float(got.get("cost") or 0)
            with open(self.path, "a") as f:
                for i, m in enumerate(miss):
                    p = got["nouls"][self.noul][str(i)]
                    self._seen[self._k(message, m)] = p
                    f.write(json.dumps({"k": self._k(message, m), "p": p}) + "\n")
        return [self._seen[self._k(message, m)] for m in memories]


def decider(spec: str, client: Cached | None = None):
    """decide(turn, candidates, ids) -> memory id to supersede, or None. `ids` maps memory id to turn."""
    name, _, arg = spec.partition(":")
    if name == "none":
        return lambda turn, cands, held: None
    if name == "oracle":
        def oracle(turn, cands, held):
            if not turn["evidence"]:
                return None
            old = [c.id for c in cands if held[c.id]["evidence"]]
            return old[0] if old else None
        return oracle
    if name == "embed":
        t = float(arg)
        return lambda turn, cands, held: next((c.id for c in cands if c.cosine >= t), None)
    if name in ("jev", "jev2"):          # jev: the `replaces` wording; jev2: `replaces_v2`
        t = float(arg)

        def j(turn, cands, held):
            if not cands:
                return None
            ps = client.replaces(turn["text"], [c.content for c in cands])
            best = max(range(len(cands)), key=lambda i: ps[i])
            return cands[best].id if ps[best] >= t else None
        return j
    raise ValueError(f"unknown decider {spec!r}")


def run_question(item: dict, encode, model_tag: str, decide, tmp: Path) -> dict:
    ts = turns(item)
    mind = Mind(tmp / f"{item['question_id']}.db", encode=encode, model_tag=model_tag)
    held: dict[int, dict] = {}
    proposals = 0
    try:
        for t in ts:
            cands = [c for c in mind.recall(t["text"], namespaces=[PLACE], k=PROPOSE_K) if c.cosine >= PROPOSE_FLOOR]
            target = decide(t, cands, held) if cands else None
            proposals += target is not None
            mid = mind.remember(t["text"], source="user", at=t["at"], key=t["key"],
                                namespaces=filing.turn_namespaces(t["sid"], PLACE), supersedes=target,
                                superseded_by_judgment_of="inference" if target is not None else None)
            held[mid] = t
        final = [r.id for r in mind.recall(item["question"], namespaces=[PLACE], k=K)]
        ev = [t for t in ts if t["evidence"]]
        new, old = ev[-1], ev[:-1]
        by_key = {t["key"]: mid for mid, t in held.items()}
        sup = {mid: mind.superseded_by(mid) for mid in held}
        old_ids = {by_key[t["key"]] for t in old}
        collateral = [{"old": held[a]["text"], "new": held[b]["text"]} for a, b in sup.items()
                      if b is not None and a not in old_ids]
        return {"question": item["question_id"], "turns": len(ts), "proposals": proposals,
                "stale_served": any(by_key[t["key"]] in final for t in old),
                "new_served": by_key[new["key"]] in final,
                "new_retired": sup[by_key[new["key"]]] is not None,
                "old_retired": all(sup[i] is not None for i in old_ids),
                "collateral": len(collateral), "collateral_pairs": collateral}
    finally:
        mind.close()


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    return {"questions": n, "stale_served": sum(r["stale_served"] for r in rows),
            "new_served": sum(r["new_served"] for r in rows),
            "new_retired": sum(r["new_retired"] for r in rows),
            "old_retired": sum(r["old_retired"] for r in rows),
            "collateral": sum(r["collateral"] for r in rows),
            "collateral_per_question": sum(r["collateral"] for r in rows) / n if n else None,
            "proposals": sum(r["proposals"] for r in rows)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--data", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--decider", required=True)
    ap.add_argument("--key-file", type=Path)
    a = ap.parse_args()
    from bench.systems.embed_cache import EmbedCache
    from tapestry import embed
    enc, cache = embed.Encoder(), EmbedCache(embed.MODEL_TAG)
    items = sample(json.loads(a.data.read_bytes()), a.n, a.seed, a.offset)
    a.out.mkdir(parents=True, exist_ok=True)
    noul = {"jev": "replaces", "jev2": "replaces_v2"}.get(a.decider.partition(":")[0])
    client = Cached(jev.Jev(a.key_file, session_id="lme-update"), a.out / f"{noul}-cache.jsonl", noul) \
        if noul else None
    decide = decider(a.decider, client)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for it in items:
            rows.append(run_question(it, lambda x: cache.encode(x, enc), embed.MODEL_TAG, decide, Path(tmp)))
    tag = a.decider.replace(":", "-")
    (a.out / f"rows-{tag}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    pairs = [{"question": r["question"], **c, "label": None} for r in rows for c in r["collateral_pairs"]]
    sheet = random.Random(a.seed).sample(pairs, min(SHEET, len(pairs)))
    (a.out / f"sheet-{tag}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in sheet))
    out = {"decider": a.decider, "seed": a.seed, "offset": a.offset, "sheet": len(sheet), **summarise(rows)}
    if client:
        out |= {"jev_calls": client.calls, "jev_cost_usd": client.cost}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
