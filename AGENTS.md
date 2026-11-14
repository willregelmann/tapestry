## What Tapestry Is

General-purpose long-term memory for any agent: a home assistant, a personal companion, a coding agent. Coding sessions are only one case, so a change that helps them and hurts the others isn't an improvement. Its design leans on how human memory works: facts kept apart from the conversations they came from, context deciding what comes to mind, belief that fades without fresh evidence. Where human memory fails, such as believing what's merely repeated, it deliberately departs.

## The Map

This project has a map in `docs/`: what it's for, what it's made of and what must always hold. Read `docs/README.md` before changing code, and work with the `develop-project` skill.

The map is our best attempt so far, not settled truth. When a measurement, real use or a better idea from how people remember says a rule is wrong, question it and propose changing it through `map-project`. That a rule was chosen deliberately is no evidence that it's right. Experiments that test the map's own rules are welcome.

## Prompts and Evals

Prefer a structural fix to more words in a prompt. A model asked to judge or draw gets a short, plain instruction; a growing list of cases it must handle is a sign the structure is wrong. Where a yes or no judgment fits, measure Jev beside the Claude model.

The evals in `bench/` are samples of the memory we want, not the target. Don't tune a prompt, a threshold or a fixture until an eval passes: a change has to make sense for memory in general, and a gain that only shows on the fixtures it was tuned on is overfitting. Results that rest on a few prompts or one run are said to be small.

## Benchmarks

Every major change to how memories are drawn, recalled or believed is benchmarked before it merges. Run the benchmarks in `bench/` that it touches with `--log`, which appends to `bench/results.jsonl`, and say what they showed in `PLAN.md`, beside what they measure. A change that made a result worse is recorded too.
