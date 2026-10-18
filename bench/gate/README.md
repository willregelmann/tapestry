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
- Consent: Will on Chat, 2026-10-17, relayed by Ash: "Let's try it. I'm not concerned about data exfiltration, so hosted is fine" (state.db message 10272; the relay, not the original). Every request also asks the gateway for zero data retention. NOT YET CHECKED: whether Vercel's docs say TypeSafe qualifies for it, and what the gateway does if no provider does. Check that before the pilot.
- Pilot (Wren's ask): 10 turns, both Nouls against the constrains Noul alone, with the difference in the constrains p stated. It is the first Jev spend. It chooses nothing: J's score is already frozen as `bears_on` (rev 8).
- Labels: Ash and Wren label blind to scores. Wren is also a reviewer of this gate; that's fine because she isn't the builder. Labelling disclosures are in the plan record, rev 5.

## Tests

`python -m pytest tests/test_gate.py` from the repo root. Jev is stubbed, and nothing touches the network. `tests/gate-arms.sh` breaks each guarantee in turn and expects red.
