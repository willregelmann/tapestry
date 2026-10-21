"""Score a LongMemEval gate run: join corpus, labels and Jev's answers, then score.report.

    python -m bench.gate.lme_report CORPUS_JSONL LABELS_JSON ANSWERS_JSONL --seed N
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bench.gate import jev, score


def rows(corpus: Path, labels: Path, answers: Path) -> tuple[list[dict], list[float]]:
    """One row per candidate Jev answered, with the label the dataset gave it."""
    lab = json.loads(labels.read_text())
    ans = {}
    for line in answers.read_text().splitlines():
        a = json.loads(line)
        ans[a["turn"]] = a
    out = []
    for line in corpus.read_text().splitlines():
        c = json.loads(line)
        a = ans.get(c["turn"])
        if a is None:
            continue
        out.append({"turn": c["turn"], "item": c["item"], "memory": c["memory"],
                    "cosine": c["cosine"], "p": a["nouls"][jev.P_PRIMARY][c["item"]],
                    **lab[c["item"]]})
    return out, [a["seconds"] for a in ans.values()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("labels", type=Path)
    ap.add_argument("answers", type=Path)
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    r, lat = rows(a.corpus, a.labels, a.answers)
    print(json.dumps(score.report(r, a.seed, lat), indent=1))


if __name__ == "__main__":
    main()
