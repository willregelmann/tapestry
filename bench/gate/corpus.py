"""Build the gate corpus from a Hermes state.db: the recall candidates each turn was actually shown.

    python -m bench.gate.corpus STATE_DB OUT_DIR --turns 80 --seed 20261017

Hermes stores the injected recall in the user message's `api_content`, inside <memory-context>.
Each candidate is one "[#N · ... · match: X · cos Y · learned ...] text" item. Writes:

  OUT_DIR/corpus.jsonl  every field, one candidate per line (the scorer reads this)
  OUT_DIR/sheet.jsonl   what a labeller sees: message, memory, reply. No cosine, no Jev.
  OUT_DIR/manifest.json the seed, the counts, and how many turns were dropped and why.

Turns are dropped, before sampling, when recall was truncated before injection (the items aren't
whole), when the message is the agent's own alarm or a compaction summary (no outside request to be
constrained), or when the turn produced no reply text (a labeller has nothing to judge by).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
from pathlib import Path

_BLOCK = re.compile(r"<memory-context>(.*?)</memory-context>", re.S)
_ITEM = re.compile(r"^\[#(\d+) · (.*?)\] ?(.*)", re.S)
_COS = re.compile(r"cos (-?[0-9.]+)")
_MATCH = re.compile(r"match: ([^·\]]+?)\s*(?:·|$)")
TRUNCATED = "prefetch output truncated"
MAX_PER_TURN = 5
LABELS = ("constrains", "informs", "irrelevant")
# Credential-shaped strings: redacted in both files before anything leaves this machine.
_SECRET = re.compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}|(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{20,}"
    r"|(?i:(?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*)['\"]?[A-Za-z0-9._~+/=-]{16,}")
REDACTED = "[REDACTED]"


def redact(text: str) -> tuple[str, int]:
    """`text` with every credential-shaped string replaced, and how many were."""
    return _SECRET.subn(REDACTED, text)


def items(block: str) -> list[dict]:
    """The candidates in one <memory-context> block, in the order recall gave them."""
    out = []
    for part in re.split(r"\n(?=\[#\d+ · )", block):
        m = _ITEM.match(part.strip())
        if not m:
            continue
        head = m.group(2)
        cos = _COS.search(head)
        match = _MATCH.search(head)
        out.append({"memory": int(m.group(1)), "text": m.group(3).strip(),
                    "cosine": float(cos.group(1)) if cos else None,
                    "match": match.group(1).strip() if match else None,
                    "rank": len(out) + 1})
    return out


def _own(message: str) -> bool:
    s = message.lstrip()
    if s.startswith("[Alarm") or s.startswith("[CONTEXT COMPACTION"):
        return True
    return s.startswith("[from ") and "woken by its own alarm" in s[:200]


def turns(db: sqlite3.Connection) -> tuple[list[dict], dict]:
    """Every turn with injected recall, with its message, candidates and final reply.

    Hermes can write one user turn twice (two rows, same message and same injected recall). A
    copy is the same data point, so only the lowest id is kept; otherwise the split can put one
    copy in dev and the other in test (Wren, on 3099edf6). A candidate whose header carries no
    cosine can't be scored by E, so it is dropped and counted here, not left to fail in scoring.
    """
    dropped = {"truncated": 0, "own_alarm_or_compaction": 0, "no_items": 0, "no_reply": 0,
               "duplicate_turn": 0, "no_cosine": 0}
    out = []
    seen: set[tuple] = set()
    sessions = [r[0] for r in db.execute(
        "SELECT DISTINCT session_id FROM messages WHERE role='user' AND api_content LIKE '%<memory-context>%'")]
    for sid in sessions:
        rows = db.execute("SELECT id, role, content, api_content FROM messages WHERE session_id=? ORDER BY id",
                          (sid,)).fetchall()
        for i, (mid, role, content, api) in enumerate(rows):
            if role != "user" or not api or "<memory-context>" not in api:
                continue
            if (sid, content, api) in seen:
                dropped["duplicate_turn"] += 1
                continue
            seen.add((sid, content, api))
            block = _BLOCK.search(api)
            if TRUNCATED in api:
                dropped["truncated"] += 1
                continue
            if _own(content or ""):
                dropped["own_alarm_or_compaction"] += 1
                continue
            cands = items(block.group(1)) if block else []
            dropped["no_cosine"] += sum(c["cosine"] is None for c in cands)
            cands = [c for c in cands if c["cosine"] is not None]
            if not cands:
                dropped["no_items"] += 1
                continue
            reply = ""
            for _, r2, c2, _ in rows[i + 1:]:
                if r2 == "user":
                    break
                if r2 == "assistant" and (c2 or "").strip():
                    reply = c2.strip()          # the last text the turn produced
            if not reply:
                # Tool calls only, or cut off by compaction: a labeller has nothing to judge by.
                dropped["no_reply"] += 1
                continue
            out.append({"turn": f"{sid}:{mid}", "message": (content or "").strip(),
                        "reply": reply, "candidates": cands[:MAX_PER_TURN]})
    return out, dropped


def build(db_path: Path, out: Path, n_turns: int, seed: int) -> dict:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        all_turns, dropped = turns(db)
    finally:
        db.close()
    rng = random.Random(seed)
    chosen = sorted(rng.sample(all_turns, min(n_turns, len(all_turns))), key=lambda t: t["turn"])
    out.mkdir(parents=True, exist_ok=True)
    n = redactions = 0
    with open(out / "corpus.jsonl", "w") as corpus, open(out / "sheet.jsonl", "w") as sheet:
        for t in chosen:
            message, r1 = redact(t["message"])
            reply, r2 = redact(t["reply"])
            redactions += r1 + r2
            for c in t["candidates"]:
                memory, r3 = redact(c["text"])
                redactions += r3
                item = f"{t['turn']}:{c['memory']}"
                corpus.write(json.dumps({"item": item, "turn": t["turn"], "message": message,
                                         "memory": memory, "cosine": c["cosine"],
                                         "match": c["match"], "rank": c["rank"]}) + "\n")
                sheet.write(json.dumps({"item": item, "message": message, "memory": memory,
                                        "reply": reply, "label": None}) + "\n")
                n += 1
    manifest = {"seed": seed, "turns_eligible": len(all_turns), "turns_sampled": len(chosen),
                "candidates": n, "dropped": dropped, "redactions": redactions,
                "labels": list(LABELS)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--turns", type=int, default=80)
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    print(json.dumps(build(a.db, a.out, a.turns, a.seed), indent=1))


if __name__ == "__main__":
    main()
