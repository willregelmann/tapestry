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
arm "model alias"            $J 'MODEL = "typesafe-ai/jev-1.13.0"' 'MODEL = "typesafe-ai/jev"'
arm "no session_id"          $J 'body["session_id"] = session_id' 'pass'
arm "build change ignored"   $J 'elif build != self.build:' 'elif False:'
arm "no slug accepted"       $J 'if not resp.get("model") or not slug:' 'if False:'
arm "answers misaligned"     $J 'a = answers.get(f"c{i}")' 'a = answers.get(f"c{len(candidates) - 1 - i}")'
arm "missing answer = 0.5"   $J 'if a is None:
                raise RuntimeError(f"Jev returned no answer for {item}")' 'if a is None:
                a = {"type": "noul", "noul": 0.5}'
arm "no ZDR"                 $J '"providerOptions": {"gateway": {"zeroDataRetention": True}},' ''
arm "any provider ok"        $J 'if provider != "typesafe-ai":' 'if False:'
arm "fallback ok"            $J 'if len(routing.get("modelAttempts") or [None]) != 1:' 'if False:'
arm "build ignores slug"     $J 'build = f"{resp['"'"'model'"'"']}|{slug}|{provider}"' 'build = resp["model"]'
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
R=bench/gate/run.py
arm "4xx retried"            $J 'if e.code in (429, 529, 500, 502, 503, 504):' 'if e.code >= 400:'
arm "429 not retried"        $J 'if e.code in (429, 529, 500, 502, 503, 504):' 'if e.code in (500, 502, 503, 504):'
arm "resume repeats calls"   $R 'if turn in seen or turn in exclude:
                continue' 'if False:
                continue'
arm "resume forgets build"   $R 'client.build = build ' 'client.build = None '
arm "no retry"               $R 'except Retryable:
                    if attempt == tries - 1:' 'except ZeroDivisionError:
                    if attempt == tries - 1:'
arm "no backoff"             $R 'sleep(2 ** attempt)' 'sleep(0)'
arm "no spend cap"           $R 'if spent >= cap_usd:' 'if False:'
arm "answer not saved"            $R 'out.write(json.dumps({"turn": turn, **got}) + "\n")' 'pass'
# Wren's review of 3099edf6
arm "duplicates kept"        $C 'if (sid, content, api) in seen:' 'if False:'
arm "dedupe across sessions" $C 'if (sid, content, api) in seen:' 'if (content, api) in {(k[1], k[2]) for k in seen}:'
arm "no-cosine kept"         $C 'cands = [c for c in cands if c["cosine"] is not None]' 'pass'
arm "verdict two-sided"      $S 'if not (fi["J"] < fi["E"] and fi["mcnemar"]["p"] < ALPHA):' 'if not fi["mcnemar"]["p"] < ALPHA:'
arm "verdict no recall gate" $S 'if rc["J"] < rc["E"] - MAX_RECALL_GAP:' 'if False:'
arm "recall gap strict"      $S 'if rc["J"] < rc["E"] - MAX_RECALL_GAP:' 'if rc["J"] <= rc["E"] - MAX_RECALL_GAP:'
arm "verdict no latency"     $S 'if not lat < MAX_P95_SECONDS:' 'if False:'
arm "latency inclusive"      $S 'if not lat < MAX_P95_SECONDS:' 'if lat > MAX_P95_SECONDS:'
arm "zero positives pass"    $S 'if not rc["of"]:' 'if False:'
arm "p95 is max"             $S 'return s[rank - 1]' 'return s[-1]'
arm "E-at-J uses dev"        $S 'sum(j[i] for i in pos)),' 'sum(j[i] for i in pos) + 1),'
arm "exclude ignored"        $S 'rows = [r for r in rows if r["turn"] not in exclude]' 'pass'
arm "J-only drop hidden"      $S 'j_only = [i for i in dropped["J"] if i not in dropped["E"]]' 'j_only = []'
arm "any drop is J's"         $S 'j_only = [i for i in dropped["J"] if i not in dropped["E"]]' 'j_only = list(dropped["J"])'
arm "pass label bare"         $S 'out["verdict"] = "PASS, with a constraint dropped"' 'pass'
arm "dropped list is E's"     $S '"J": [test[i].get("item", test[i]["turn"]) for i in pos if not j[i]]}' '"J": [test[i].get("item", test[i]["turn"]) for i in pos if not e[i]]}'
R=bench/gate/run.py
arm "run ignores exclude"     $R 'if turn in seen or turn in exclude:' 'if turn in seen:'
arm "exclude optional"        $R 'ap.add_argument("--exclude", type=Path, required=True)' 'ap.add_argument("--exclude", type=Path, default=Path("/dev/null"))'
echo "arms red: $red, bad: $bad"
[ $bad = 0 ]
