# Jev as a recall-injection gate: an offline measurement

The question: after the embedding match picks a turn's candidates, does a yes/no question to Jev ("does this memory constrain what the agent should do here?") keep the memories that matter while dropping the ones that don't, better than embedding similarity alone? It's measured against labels people wrote, never against Jev's own probabilities. The plan of record is /home/foil/work/reviews/tapestry-jev-gate-plan-2.md (Foil, revision 2).

Nothing here changes `Mind.recall`. A gate goes into recall only if this measurement shows something.

## Steps

1. Corpus. `python -m bench.gate.corpus STATE_DB OUT --turns 80 --seed 20261017` writes three files:
   - `corpus.jsonl`, the scorer's input;
   - `sheet.jsonl`, the labellers' view: message, memory and reply, with no cosine and no Jev;
   - `manifest.json`.

   Turns are dropped before sampling when recall was truncated, when the message is the agent's own alarm or a compaction summary, or when the turn produced no reply text. The output holds transcript text, so write it to a private directory.
2. Labels. Each labeller fills `label` with "constrains", "informs" or "irrelevant". Two labellers share an overlap of 100 items. `score.agreement` reports percent agreement and kappa, three-way and binary; below 0.6 binary kappa the question gets reworded before anything is measured. The corpus is frozen with its sha before anyone sees it and is never rebuilt, because the store keeps growing.
3. Jev. `jev.Jev(key_file).gate(message, [(item, memory), …])` sends one request per turn: the state is the message, with one Noul per candidate. Calls go to the Vercel AI Gateway (`/typesafe/v1/systemone`), with the model pinned to `typesafe-ai/jev-1.13.0`. The dated build that answered is recorded, and a change of build mid-run stops the run.
4. Score. `score.evaluate(rows, seed)` splits dev and test by turn, then tunes each arm's threshold on dev, as the highest that keeps 90% of the "constrains" items. On test it reports the primary result (among items not labelled "constrains", paired exact McNemar on E's and J's decisions), the achieved recall of "constrains" items, the share of "informs" each arm drops, AUROC and the base rate.

## What "shows something" means (fixed before any Jev call)

On test, all three of these must hold, and `score.verdict(result, latencies)` computes them:
- the primary result, J against E, with J injecting FEWER non-constraining items and p < 0.05 (the McNemar is two-sided, so the direction is checked in code, not read off the p-value);
- J's recall of the "constrains" items no more than 5 points below E's, and at least one positive on test;
- Jev p95 latency under 1 s. This is measured from the workstation. Production recall runs on ha-pi, where recall alone has a median of 2.8 s, so this gate says nothing about ha-pi.

Anything else is NULL. A PASS where J alone drops a "constrains" item that E keeps is reported as "PASS, with a constraint dropped" with the ids, never as a bare PASS (rev 5). Every "constrains" item either arm drops on test is listed by id in the result. Function 2 (supersession proposals) starts only after a pass.

Also reported, descriptive and never deciding: E's false injections at the cosine that gives E the same test recall as J achieved, and an "inject all" row (today's behaviour).

## Before any Jev call

- `frozen/exclude.json` (sha256 `f99d63c8f698bead57f280f3916f07662456d76974f62d3a831100cd34099d63`) lists nine turns that never go to Jev and are removed before the split: one duplicate of another turn, the five turns of the money-agent conversation (plus one more that relays it), and two turns carrying a balance-like figure. `bench.gate.run` requires it (`--exclude`), and `score.evaluate(exclude=)` applies it to scoring. Check the sha before running.
- Consent: Will on Chat, 2026-10-17, relayed by Ash: "Let's try it. I'm not concerned about data exfiltration, so hosted is fine" (state.db message 10272; the relay, not the original). Every request also asks the gateway for zero data retention. NOT YET CHECKED: whether Vercel's docs say TypeSafe qualifies for it, and what the gateway does if no provider does. Check that before the pilot.
- Pilot (Wren's ask): 10 turns, both Nouls against the constrains Noul alone, with the difference in p stated. It is the first Jev spend.
- Labels: Ash and Wren label blind to scores. Wren is also a reviewer of this gate; that's fine because she isn't the builder. Labelling disclosures are in the plan record, rev 5.

## Tests

`python -m pytest tests/test_gate.py` from the repo root. Jev is stubbed, and nothing touches the network. `tests/gate-arms.sh` breaks each guarantee in turn and expects red.
