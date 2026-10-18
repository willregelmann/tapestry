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
arm "sheet shows cosine"     $C '"reply": reply, "label": None}' '"reply": reply, "label": None, "cosine": c["cosine"]}'
arm "seed ignored"           $C 'rng = random.Random(seed)' 'rng = random.Random()'
arm "jev sees reply"         $C '"memory": memory, "cosine": c["cosine"],' '"memory": memory + " " + reply, "cosine": c["cosine"],'
arm "memory not redacted"    $C 'memory, r3 = redact(c["text"])' 'memory, r3 = c["text"], 0'
arm "reply not redacted"     $C 'reply, r2 = redact(t["reply"])' 'reply, r2 = t["reply"], 0'
arm "message not redacted"   $C 'message, r1 = redact(t["message"])' 'message, r1 = t["message"], 0'
arm "no bearer pattern"      $C '|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{20,}' ''
arm "sk- unanchored"         $C 'r"(?<![A-Za-z0-9])sk-' 'r"sk-'
arm "jev sees cosine"        $J '"instructions": {"memory": text, "question": QUESTION}}' '"instructions": {"memory": text, "question": QUESTION, "cosine": 0.71}}'
arm "model alias"            $J 'MODEL = "typesafe/jev-1.13"' 'MODEL = "~typesafe/jev-latest"'
arm "no session_id"          $J 'body["session_id"] = session_id' 'pass'
arm "build change ignored"   $J 'elif build != self.build:' 'elif False:'
arm "no model accepted"      $J 'if not build:
            raise' 'if False:
            raise'
arm "answers misaligned"     $J 'a = answers.get(f"c{i}")' 'a = answers.get(f"c{len(candidates) - 1 - i}")'
arm "missing answer = 0.5"   $J 'if a is None:
                raise RuntimeError(f"Jev returned no answer for {item}")' 'if a is None:
                a = {"type": "noul", "noul": 0.5}'
arm "malformed accepted"     $J 'if a.get("type") != "noul" or not' 'if False and not'
arm "empty key accepted"     $J 'if not self._key:' 'if False:'
arm "split overlaps"         $S 'return set(order[:half]), set(order[half:])' 'return set(order[:half + 1]), set(order[half:])'
arm "threshold keeps 80%"    $S 'need = -(-len(pos) * keep // 1)' 'need = -(-len(pos) * (keep - 0.1) // 1)'
arm "tie not injected"       $S 'e = [r["cosine"] >= t_e for r in test]' 'e = [r["cosine"] > t_e for r in test]'
arm "mcnemar one-sided"      $S 'p = min(1.0, 2 * sum(' 'p = min(1.0, 1 * sum('
arm "tuned on test"          $S 'y_dev = [r["label"] == "constrains" for r in dev]' 'dev = test; y_dev = [r["label"] == "constrains" for r in dev]'
arm "false inject on all"    $S '"false_inject": {"E": sum(e[i] for i in neg), "J": sum(j[i] for i in neg)' '"false_inject": {"E": sum(e), "J": sum(j)'
arm "informs counted as ok"  $S 'neg = [i for i, v in enumerate(lab) if v != "constrains"]' 'neg = [i for i, v in enumerate(lab) if v == "irrelevant"]'
arm "informs_dropped wrong"  $S '"J": _rate(sum(not j[i] for i in inf), len(inf))' '"J": _rate(sum(j[i] for i in inf), len(inf))'
arm "unlabelled scored"      $S 'if r.get("label") not in LABELS:' 'if False:'
arm "kappa_3way uses binary" $S '"kappa_3way": kappa(a, b, LABELS),' '"kappa_3way": kappa(ab, bb, (True, False)),'
echo "arms red: $red, bad: $bad"
[ $bad = 0 ]
