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
arm "jev sees cosine"        $J '"question": NOULS[n]["question"]}}' '"question": NOULS[n]["question"], "cosine": 0.71}}'
arm "model alias"            $J 'MODEL = "typesafe-ai/jev-1.13.0"' 'MODEL = "typesafe-ai/jev"'
arm "no session_id"          $J 'body["session_id"] = session_id' 'pass'
arm "build change ignored"   $J 'elif build != self.build:' 'elif False:'
arm "no slug accepted"       $J 'if not resp.get("model") or not slug:' 'if False:'
arm "answers misaligned"     $J 'a = answers.get(f"{n}:{i}")' 'a = answers.get(f"{n}:{len(candidates) - 1 - i}")'
arm "missing answer = 0.5"   $J 'if a is None:
                    raise RuntimeError(f"Jev returned no {n} answer for {item}")' 'if a is None:
                    a = {"type": "noul", "noul": 0.5}'
arm "ZDR flag sent"          $J 'ZDR_FLAG = False' 'ZDR_FLAG = True'
arm "no provider restriction" $J 'gateway: dict = {"only": list(ONLY)}' 'gateway: dict = {}'
arm "restriction widened"    $J 'ONLY = ["typesafe-ai"]' 'ONLY = ["typesafe-ai", "openai"]'
arm "restriction aliased"    $J 'gateway: dict = {"only": list(ONLY)}' 'gateway: dict = {"only": ONLY}'
arm "any provider ok"        $J 'if provider != "typesafe-ai":' 'if False:'
arm "fallback ok"            $J 'if len(routing.get("modelAttempts") or [None]) != 1:' 'if False:'
arm "build ignores slug"     $J 'build = f"{resp['"'"'model'"'"']}|{slug}|{provider}"' 'build = resp["model"]'
arm "malformed accepted"     $J 'if a.get("type") != "noul" or not' 'if False and not'
arm "empty key accepted"     $J 'if not self._key:' 'if False:'
arm "split overlaps"         $S 'return set(order[:half]), set(order[half:])' 'return set(order[:half + 1]), set(order[half:])'
arm "threshold keeps 80%"    $S 'need = -(-len(pos) * keep // 1)' 'need = -(-len(pos) * (keep - 0.1) // 1)'
arm "tie not injected"       $S 'e = [r["cosine"] >= t_e for r in test]' 'e = [r["cosine"] > t_e for r in test]'
arm "mcnemar one-sided"      $S 'p = min(1.0, 2 * sum(' 'p = min(1.0, 1 * sum('
arm "tuned on test"          $S 'y_dev = [r["label"] in POSITIVE for r in dev]' 'dev = test; y_dev = [r["label"] in POSITIVE for r in dev]'
arm "false inject on all"    $S '"false_inject": {"E": sum(e[i] for i in neg), "J": sum(j[i] for i in neg)' '"false_inject": {"E": sum(e), "J": sum(j)'
arm "unlabelled scored"      $S 'if r.get("label") not in LABELS:' 'if False:'
arm "kappa_3way uses binary" $S '"kappa_3way": _kappa(a, b, LABELS),' '"kappa_3way": _kappa(ab, bb, (True, False)),'
# Wren's review of d21a36d0 (462a643c): the gate is relevance, not constrains (rev 4)
arm "positive = constrains"  $S 'POSITIVE = frozenset({"constrains", "informs"})' 'POSITIVE = frozenset({"constrains"})'
arm "positive = informs"     $S 'POSITIVE = frozenset({"constrains", "informs"})' 'POSITIVE = frozenset({"informs"})'
arm "tune on constrains"     $S 'y_dev = [r["label"] in POSITIVE for r in dev]' 'y_dev = [r["label"] == "constrains" for r in dev]'
arm "test class constrains"  $S 'y = [v in POSITIVE for v in lab]' 'y = [v == "constrains" for v in lab]'
arm "kappa gate constrains"  $S 'ab = [v in POSITIVE for v in a]' 'ab = [v == "constrains" for v in a]'
arm "kappa gate inclusive"   $S 'out["gate_ok"] = round(out["kappa_binary"], 9) >= MIN_KAPPA' 'out["gate_ok"] = round(out["kappa_binary"], 9) > MIN_KAPPA'
arm "kappa gate on 3way"     $S 'out["gate_ok"] = round(out["kappa_binary"], 9) >= MIN_KAPPA' 'out["gate_ok"] = round(out["kappa_3way"], 9) >= MIN_KAPPA'
arm "stale kept in primary"  $S 'rows = [r for r in rows if not r.get("stale")]' 'pass'
arm "stale moves the split"  $S 'dev_turns, test_turns = split([r["turn"] for r in rows], seed)
    n_stale' 'n_stale'
arm "stale on relevant ok"   $S 'if r.get("stale") and r["label"] != "irrelevant":' 'if False:'
arm "secondary = primary"    $S 'secondary = evaluate(rows, seed, exclude=exclude, stale="include")' 'secondary = evaluate(rows, seed, exclude=exclude, stale="exclude")'
arm "verdict on secondary"   $S '"verdict": verdict(primary, latencies)' '"verdict": verdict(secondary, latencies)'
arm "stale agree on all"     $S 'both = [i for i in range(len(a)) if a[i] == "irrelevant" and b[i] == "irrelevant"]' 'both = list(range(len(a)))'
arm "merge keeps disagree"   $S 'if (x["label"] in POSITIVE) != (y["label"] in POSITIVE):
            drop.append(item)
            continue' 'if False:
            continue'
arm "merge picks a"          $S 'lab = "constrains" if "constrains" in (x["label"], y["label"]) else "informs"' 'lab = x["label"]'
arm "merge stale needs both" $S '"stale": bool(x.get("stale") or y.get("stale"))' '"stale": bool(x.get("stale") and y.get("stale"))'
arm "underpowered decides"   $S 'if dc["n"] < MIN_DISCORDANT:' 'if False:'
arm "underpowered at 6"      $S 'MIN_DISCORDANT = 6 ' 'MIN_DISCORDANT = 7 '
arm "saturated wording off"  $S 'if e_irr is not None and e_irr >= E_SATURATED:' 'if False:'
arm "saturated exclusive"    $S 'if e_irr is not None and e_irr >= E_SATURATED:' 'if e_irr is not None and e_irr > E_SATURATED:'
arm "chars not counted"      $S 'return {"items": len(c), "chars": None if None in c else sum(c)}' 'return {"items": len(c), "chars": 0}'
arm "pass line drops E all"  $S '[E injects {e_rates['"'"'all'"'"']:.1%} of test items, ' '['
arm "pass line drops E irr"  $S '{e_irr:.1%} of irrelevant; ' ''
arm "pass line no baseline"  $S 'inject-all would inject all {result['"'"'descriptive_inject_all'"'"']['"'"'false_inject'"'"']} irrelevant]' ']'
arm "constrains recall = all" $S '"J": _rate(sum(j[i] for i in con), len(con))}},' '"J": _rate(sum(j[i] for i in pos), len(pos))}},'
J=bench/gate/jev.py
arm "primary is constrains"  $J 'P_PRIMARY = "bears_on"' 'P_PRIMARY = "constrains"'
arm "one noul only"          $J 'BOTH = ("bears_on", "constrains")' 'BOTH = ("bears_on",)'
arm "noul keys collide"      $J 'f"{n}:{i}": {"type": "noul"' 'f"c{i}": {"type": "noul"'
arm "answer read cross-noul" $J 'a = answers.get(f"{n}:{i}")' 'a = answers.get(f"{n}:{i}") or answers.get(f"constrains:{i}")'
arm "same question twice"    $J '"question": NOULS[n]["question"]}}' '"question": NOULS["constrains"]["question"]}}'
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
arm "cap ignores reported"   $R 'return max(est, reported)' 'return est'
arm "cap ignores estimate"   $R 'return max(est, reported)' 'return reported'
arm "merge count hidden"     $S '"dropped_disagree": None if merged is None else merged["dropped_disagree"]}' '"dropped_disagree": None}'
arm "bears_on topical"       $J 'or it states a rule, fact or commitment that applies to this ' 'or '
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
arm "pass label bare"         $S 'v += ", with a constraint dropped"' 'pass'
arm "dropped list is E's"     $S '"J": [ident[i] for i in con if not j[i]]}' '"J": [ident[i] for i in con if not e[i]]}'
R=bench/gate/run.py
arm "run ignores exclude"     $R 'if turn in seen or turn in exclude:' 'if turn in seen:'
arm "exclude optional"        $R 'ap.add_argument("--exclude", type=Path, required=True)' 'ap.add_argument("--exclude", type=Path, default=Path("/dev/null"))'
echo "arms red: $red, bad: $bad"
[ $bad = 0 ]
