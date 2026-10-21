# Jev as a recall-injection gate: an offline measurement

The question: after the embedding match picks a turn's candidates, does asking Jev "does this memory bear on this message?" keep the relevant memories while dropping the irrelevant ones, better than embedding similarity alone? It's a relevance gate over injected context, and it measures nothing about reply quality. It's measured against labels people wrote, never against Jev's own probabilities. The plan of record is /home/foil/work/reviews/tapestry-jev-gate-plan-3.md, as amended by revs 4 to 9 (the `*-amendment.md` files beside it).

Nothing here changes `Mind.recall`. A gate goes into recall only if this measurement shows something.

## Steps

1. Corpus. `python -m bench.gate.corpus STATE_DB OUT --turns 80 --seed 20261017` writes three files:
   - `corpus.jsonl`, the scorer's input;
   - `sheet.jsonl`, the labellers' view: message, memory and reply, with no cosine and no Jev;
   - `manifest.json`.

   Turns are dropped before sampling when recall was truncated, when the message is the agent's own alarm or a compaction summary, or when the turn produced no reply text. The output holds transcript text, so write it to a private directory.
2. Labels. Each labeller fills `label` with "constrains", "informs" or "irrelevant", and adds `"stale": true` to an irrelevant item that is irrelevant only because it's out of date. Relevant means constrains or informs (`score.POSITIVE`). Two labellers share an overlap. `score.agreement` gates on kappa for relevant against irrelevant: below 0.6, the question is reworded before anything is measured. Three-way, constrains-against-rest and stale agreement are reported too. `score.merge` makes one label per item: an overlap item the labellers put on opposite sides of the gate is dropped and counted, never adjudicated. The corpus is frozen with its sha before anyone sees it and is never rebuilt, because the store keeps growing.
3. Jev. `jev.Jev(key_file).gate(message, [(item, memory), …])` sends one request per turn. The state is the message, and each candidate gets two Nouls, `bears_on:i` and `constrains:i`. J's score is `bears_on` (`jev.P_PRIMARY`, frozen); `constrains` is descriptive. Calls go to the Vercel AI Gateway (`/typesafe/v1/systemone`), with the model pinned to `typesafe-ai/jev-1.13.0`. What answered is recorded from the routing metadata, and a change mid-run stops the run.
4. Score. `score.report(rows, seed, latencies, exclude)` splits dev and test by turn, then tunes each arm's threshold on dev as the highest that keeps 90% of the relevant items. The primary excludes stale items; the including-stale secondary uses the same split. On test it reports the primary (among irrelevant items, paired exact McNemar on E's and J's decisions), with the discordant count and split first, then the achieved recall of relevant items, injection rates, context not injected, AUROC and the base rate. Constrains is reported per arm, with every constrains item each arm drops listed by id.

## What "shows something" means (fixed before any Jev call)

On test, with stale items excluded, all three of these must hold, and `score.verdict(result, latencies)` computes them:
- the primary result, J against E, with J injecting FEWER irrelevant items and p < 0.05 (the McNemar is two-sided, so the direction is checked in code, not read off the p-value);
- J's recall of relevant items no more than 5 points below E's, and at least one relevant item on test;
- Jev p95 latency under 1 s. This is measured from the workstation. Production recall runs on ha-pi, where recall alone has a median of 2.8 s, so this gate says nothing about ha-pi.

With five or fewer discordant pairs the result is "NO DECISION: underpowered", never a null. Anything else that fails is NULL. A pass is qualified, never bare, when either of these holds:
- J alone drops a "constrains" item that E keeps: "…, with a constraint dropped", with the ids (rev 5);
- E's threshold injects 95% or more of irrelevant items: "PASS: J gates where cosine cannot …" (rev 6).

Every PASS line also prints E's overall injection rate, E's rate on irrelevant items and the inject-all count (rev 7). Function 2 (supersession proposals) starts only after a pass.

Also reported, descriptive and never deciding: E's false injections at the cosine that gives E the same test recall as J achieved, and an "inject all" row (today's behaviour).

## Before any Jev call

- `frozen/exclude.json` (sha256 `f99d63c8f698bead57f280f3916f07662456d76974f62d3a831100cd34099d63`) lists nine turns that never go to Jev and are removed before the split: one duplicate of another turn, the five turns of the money-agent conversation (plus one more that relays it), and two turns carrying a balance-like figure. `bench.gate.run` requires it (`--exclude`), and `score.evaluate(exclude=)` applies it to scoring. Check the sha before running.
- Consent: Will on Chat, relayed verbatim by Ash. First "I'm not concerned about data exfiltration, so hosted is fine" (2026-10-17), which answered whether a hosted route was acceptable, not whether real transcripts may go. Then "Yes" (2026-10-18, relays 35c49586 and 99076f7c): Foil's own credential-redacted transcripts from Foil's state.db, through the Vercel AI Gateway to TypeSafe, for this experiment only. No other agent's store, and no later use. Recorded in `frozen/CONSENT.md`.
- No zero-data-retention flag. Will's decision (b), 2026-10-18 (relayed verbatim by Ash: "And yes, go with (b)."): paid credits only, and requests carry NO `providerOptions.gateway.zeroDataRetention`. Why: the key's Vercel team is on the Hobby plan, and the gateway refuses the flag there. Ran 16:01, HTTP 403 `ZdrUnauthorizedError`: "Zero Data Retention (ZDR) is only available for Pro and Enterprise plans. Current plan: hobby." His first yes assumed the flag; (b) is a weaker protection than that, and he chose it knowing so. What protection remains rests on policy text only, and none of it is verified by us:
  - Vercel's `/v1/models` lists `typesafe-ai/jev-1.13.0` with `zdr: "all"` and `no_training: "all"`. That is the gateway's own claim.
  - Vercel's ZDR docs list TypeSafe AI as ZDR-compliant and no-training. TypeSafe's terms there allow retention "as necessary to comply with its legal obligations".
  - Vercel states the gateway itself retains no prompts.
  Without the flag, the gateway does not restrict routing to ZDR providers. The client's routing check (TypeSafe as final provider, a single attempt) is what keeps any other provider out.
- Paid credits. Free-tier credits don't serve Jev: ran 16:02, HTTP 403 `RestrictedModelsError` "Free tier users do not have access to this model". Will tops up himself; nothing real is sent until he confirms and the synthetic probe passes.
- Pilot (Wren's ask): 10 turns, both Nouls against the constrains Noul alone, with the difference in the constrains p stated. It is the first Jev spend. It chooses nothing: J's score is already frozen as `bears_on` (rev 8).
- Labels: Ash and Wren label blind to scores. Wren is also a reviewer of this gate; that's fine because she isn't the builder. Labelling disclosures are in the plan record, rev 5.

## Tests

`python -m pytest tests/test_gate.py` from the repo root. Jev is stubbed, and nothing touches the network. `tests/gate-arms.sh` breaks each guarantee in turn and expects red.

## LongMemEval variant (2026-10-18): the same question on public data

Foil's `state.db` and the consent and exclusion files that went with it live on another machine, and the consent covers Foil's transcripts only, so it doesn't extend to anyone else's. This variant needs neither. `python -m bench.gate.lme_corpus OUT --per-type 15 --seed 20261018` builds the scorer's input from LongMemEval (public), with the dataset's own `has_answer` marks as labels. Each question gets a Mind, every user turn of its haystack is remembered as a host would file it (session namespace plus a shared place), and the candidates are what `Mind.recall` returns (hybrid, k=5). `python -m bench.gate.run` (with `{"turns": []}` as the exclude file) runs Jev, and `python -m bench.gate.lme_report CORPUS LABELS ANSWERS --seed 20261018` scores it. The scorer, the split, the threshold rule and `verdict()` are unchanged, and were fixed before this corpus existed. A first version took the top 5 by cosine alone; it was run once (90 questions, PASS) and then replaced by the `Mind.recall` version, which is the one that counts. Frozen before the first Jev call on the `Mind.recall` corpus: seed 20261018, 15 questions per type, `bears_on` as the score.

What it does and doesn't show:
- It shows whether `bears_on` separates evidence turns from the other candidates cosine picks, on questions people wrote about chat histories they wrote. It doesn't show anything about Foil's memories or Tapestry's real recall, and a pass here doesn't replace the Hermes measurement.
- The label is "this turn holds the answer", not "this memory bears on this message". An on-topic turn that isn't evidence counts as irrelevant, so J is charged for calling it relevant. That favours E: a PASS is conservative, a NULL is ambiguous.
- There is no "constrains" label and no stale item, so those rows are empty. The assistant-evidence question type is skipped (memories are user turns), and evidence outside recall's top 5 is never a candidate, so recall here is recall within the shortlist. E's cosine is the cosine `Mind.recall` reports.
- Questions whose evidence isn't in the top 5 contribute only irrelevant items, as turns without a relevant memory do in real recall.

## Remembering: does a correction retire what it replaces? (`bench/gate/lme_update.py`)

The gate decides what to inject. This asks the write side's question on LongMemEval's 78 knowledge-update questions, which hold an old value and, later, a new one (`has_answer` marks both). Each question gets a Mind and its user turns are remembered in date order (the haystack list is not in date order). Before each turn is remembered, a decider may propose that it supersedes one of the memories recall holds: the 10 nearest current memories at cosine 0.30 or better. After the last turn the question is recalled at k=5 and the outcome is read off: whether an older evidence turn is still served (a stale serve), whether the newest evidence turn is served, whether it was itself retired, and how many other memories were superseded ("collateral"). `none` is today, `oracle` retires the old evidence turn when the new one arrives (a ceiling that checks the plumbing), `embed:T` supersedes the nearest candidate at cosine T or more, and `jev:P` asks Jev a `replaces` Noul per candidate and supersedes the best one at probability P or more.

Frozen before any Jev call:
- The candidate rule above was set after seeing that the first rule (k=5, cosine 0.45) put the old evidence turn in reach for only 14 of 25 questions, on the first 25 of seed 20261018's shuffle. It is scored only on the next 25 (`--offset 25`). Jev runs once, at P = 0.5; other values are reported as sensitivity, never as the result.
- `replaces` is `jev.NOULS["replaces"]`, worded before it was run. Spend is capped at $3.
- There is no PASS or NULL. The dataset labels only the old and new evidence turns, so collateral can't be called right or wrong here. The claim is limited to stale serves and retired new values against `none`, `oracle` and `embed`. Up to 40 collateral pairs per decider go to `sheet-*.jsonl` for a person to mark kept or wrongly retired, and until that's done "safe to supersede" isn't claimed.
- What it doesn't show: whether a model deciding on its own would supersede this well on messier, real memories; a session that doesn't see the old turn; or anything about belief, which is Stage 2. ADR-0018 has the agent name the memory it replaces, so Jev here is a candidate for a proposer, not the current design.

### Second attempt: `replaces_v2` (2026-10-18), fresh questions

The first wording (`replaces`) retired about 19 unrelated memories per question at P = 0.5, 4 at 0.8 and 1 at 0.9, on questions 25 to 49 of the shuffle (`--offset 25`). Reading its collateral, most of what it kept firing on was a later turn deciding or answering what an earlier one raised ("I'll go with the black boots" retiring "which boots?"). `replaces_v2` is reworded from docs/primitives/MEMORY.md, where what is superseded is a value for something that holds one value at a time, and says outright that an answer, a decision, a follow-up or an added detail isn't a replacement. That is stricter than "the memory is no longer current": a plan that became a done deal ("thinking of a charger" to "I've already got one") now counts as not replaced, and the oracle's ceiling doesn't change.

Frozen before any `replaces_v2` call:
- It's scored on the 20 questions the earlier attempts never touched (`--offset 50`, the rest of the 70 eligible), because the wording was written after reading the collateral on questions 25 to 49. Those 25 are no longer unseen. The candidate rule is unchanged.
- Primary: `jev2:0.5`, the same P as the first attempt. 0.8 and 0.9 are sensitivity. `none`, `oracle`, `embed:0.75` and the first wording at 0.5 and 0.8 are run on the same 20 for comparison.
- Success is stated as a comparison, since collateral can't be judged by the dataset: at P = 0.5 the new wording should serve fewer stale values than `none` while superseding clearly fewer unrelated memories than the first wording on the same questions, and retire no new value. Whether the fewer supersessions are the right ones still needs the labelled sheet.
- With 20 questions a difference of one or two is noise; the result is read as a direction, not a rate.

## Conflicts: does the judge flag the pair a correction makes? (`bench/gate/lme_conflicts.py`)

The conflict job (`tapestry judge`, `docs/capabilities/RECALL.md`: two memories that each claim to be current and conflict are shown together) asks Jev `conflicts_v1` about a lasting memory and its nearest neighbours, and flags a pair at probability 0.9 or more. Neither the wording nor the threshold has been measured. This runs the production path (`jobs.judge_pending` with `tapestry.judge.JevJudge`) over LongMemEval's 78 knowledge-update questions, whose old and new evidence turns are the pairs the dataset says are in conflict. Public data, so it needs no consent beyond the key.

Frozen before any call:
- Seed 20261019, 25 questions from offset 0 (the same 70 eligible questions as the supersession runs, shuffled with a different seed). Turns are lasting memories filed in the shared place only; the job is run after each session's turns.
- `conflicts_v1` and its thresholds as they are in `tapestry/judge.py`; nothing is tuned on these questions. 0.9 is the reported result; 0.5 and 0.7 are sensitivity.
- Spend is capped at $3 (resumable from the cache). `--dry-run` counts the requests first and sends none.
- No pass or fail. The claim is recall (the true pair flagged, over true pairs the job was shown) and collateral (other pairs flagged, per question and per 100 other pairs). Precision on collateral needs a person to label `sheet.jsonl` (up to 40 flagged pairs); until then it isn't claimed.

What it doesn't show:
- Whether Jev finds conflicts in real memories, which are messier than a user saying "I now have 4 dogs".
- A true pair outside a memory's 10 nearest neighbours at cosine 0.30 is never judged. That is reported as unreachable, not as missed.
- Two old evidence turns that disagree with each other are neither true pairs nor collateral; they're reported apart.
- Whether a flagged pair is a mistake is the labelled sheet's question, not the dataset's.
