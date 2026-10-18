# Jev as a recall-injection gate: an offline measurement

The question: after the embedding match picks a turn's candidates, does a yes/no question to Jev ("does this memory constrain what the agent should do here?") keep the memories that matter while dropping the ones that don't, better than embedding similarity alone? It's measured against labels people wrote, never against Jev's own probabilities. The plan of record is /home/foil/work/reviews/tapestry-jev-gate-plan.md (Foil, sha256 40584bf6…6676).

Nothing here changes `Mind.recall`. A gate goes into recall only if this measurement shows something.

## Steps

1. Corpus. `python -m bench.gate.corpus STATE_DB OUT --turns 80 --seed 20261017` writes three files:
   - `corpus.jsonl`, the scorer's input;
   - `sheet.jsonl`, the labellers' view: message, memory and reply, with no cosine and no Jev;
   - `manifest.json`.

   Turns are dropped before sampling when recall was truncated, when the message is the agent's own alarm or a compaction summary, or when the turn produced no reply text. The output holds transcript text, so write it to a private directory.
2. Labels. Each labeller fills `constrains` with true or false: "does this memory constrain what the agent should do here?" Two labellers share an overlap of 100 items. `score.kappa` is reported on the overlap, and below 0.6 the question gets reworded before anything is measured.
3. Jev. `jev.Jev(key_file).gate(message, [(item, memory), …])` sends one request per turn: the state is the message, with one Noul per candidate. The model is pinned to `typesafe/jev-1.13`, and the id of the model that answered is recorded.
4. Score. `score.evaluate(rows, seed)` splits dev and test by turn, then tunes each arm's threshold on dev, as the highest that keeps 90% of the "constrains" items. On test it reports false injections (primary, exact McNemar), the "constrains" items each arm kept, AUROC and the base rate.

## What "shows something" means (fixed before any Jev call)

On test, all three of these must hold:
- false injections, J against E, at p < 0.05 in J's favour;
- J's recall of the "constrains" items no more than 5 points below E's;
- Jev p95 latency under 1 s.

Anything else is a null. Function 2 (supersession proposals) starts only after a pass.

## Tests

`python -m pytest tests/test_gate.py` from the repo root. Jev is stubbed, and nothing touches the network. `tests/gate-arms.sh` breaks each guarantee in turn and expects red.
