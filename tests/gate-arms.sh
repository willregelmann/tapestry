#!/bin/bash
# Mutation arms for bench/gate: each breaks one guarantee and must turn tests/test_gate.py red.
# cmp-guarded restore; an anchor not found exactly once is exit 3.
set -u
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
run() { "$PY" -m pytest -q -p no:cacheprovider tests/test_gate.py >/dev/null 2>&1; }
run || { echo "not green unarmed"; exit 2; }
red=0; bad=0
arm() {  # name file old new
  local f=$2 bak; bak=$(mktemp); cp -p "$f" "$bak"
  "$PY" - "$f" "$3" "$4" <<'EOF' || { cp -p "$bak" "$f"; rm -f "$bak"; exit 3; }
import sys; p, old, new = sys.argv[1:4]; s = open(p).read()
if s.count(old) != 1: print(f"anchor x{s.count(old)}: {old[:60]!r}"); sys.exit(3)
open(p, "w").write(s.replace(old, new))
EOF
  if run; then echo "GREEN (bad): $1"; bad=$((bad+1)); else echo "red: $1"; red=$((red+1)); fi
  cp -p "$bak" "$f"; cmp -s "$bak" "$f" || { echo "RESTORE FAILED $f"; exit 4; }; rm -f "$bak"
}
C=bench/gate/corpus.py; J=bench/gate/jev.py; S=bench/gate/score.py
arm "alarms kept"            $C 'return True
    return s.startswith("[from ")' 'return False
    return s.startswith("[from ")'
arm "truncated kept"         $C 'if TRUNCATED in api:' 'if False:'
arm "reply = first text"     $C 'reply = c2.strip()          # the last text the turn produced' 'reply = reply or c2.strip()'
arm "no-reply kept"         $C 'if not reply:
                # Tool' 'if False:
                # Tool'
arm "sheet shows cosine"     $C '"reply": t["reply"], "constrains": None}' '"reply": t["reply"], "constrains": None, "cosine": c["cosine"]}'
arm "seed ignored"           $C 'rng = random.Random(seed)' 'rng = random.Random()'
arm "jev sees reply"         $C '"memory": c["text"], "cosine": c["cosine"],' '"memory": c["text"] + " " + t["reply"], "cosine": c["cosine"],'
arm "jev sees cosine"        $J '"instructions": {"memory": text, "question": QUESTION}}' '"instructions": {"memory": text, "question": QUESTION, "cosine": 0.71}}'
arm "model alias"            $J 'MODEL = "typesafe/jev-1.13"' 'MODEL = "~typesafe/jev-latest"'
arm "answers misaligned"     $J 'a = answers[f"c{i}"]' 'a = answers[f"c{len(candidates) - 1 - i}"]'
arm "malformed accepted"     $J 'if a.get("type") != "noul" or not' 'if False and not'
arm "empty key accepted"     $J 'if not self._key:' 'if False:'
arm "split overlaps"         $S 'return set(order[:half]), set(order[half:])' 'return set(order[:half + 1]), set(order[half:])'
arm "threshold keeps 80%"    $S 'need = -(-len(pos) * keep // 1)' 'need = -(-len(pos) * (keep - 0.1) // 1)'
arm "mcnemar one-sided"      $S 'p = min(1.0, 2 * sum(' 'p = min(1.0, 1 * sum('
arm "tuned on test"          $S 'y_dev = [r["constrains"] for r in dev]' 'dev = test; y_dev = [r["constrains"] for r in dev]'
arm "false inject on all"    $S '"false_inject": {"E": sum(e[i] for i in neg), "J": sum(j[i] for i in neg)' '"false_inject": {"E": sum(e), "J": sum(j)'
echo "arms red: $red, bad: $bad"
[ $bad = 0 ]
