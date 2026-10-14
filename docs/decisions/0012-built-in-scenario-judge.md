# Scenarios are judged by a judge built into the harness

## Context and Problem Statement

Scenarios produce transcripts of a real agent using memory, and something has to decide whether each run behaved as required. The first version generated a test per run for fuzzy-assertions, a separate tool, and turned its verdicts into pass rates in a second step. What should judge the scenarios?

## Decision Drivers

* One command runs, judges and reports a scenario.
* Each assertion gets a structured verdict with the evidence for it.
* A judge that fails is reported as a failure of the judge, never as a pass.

## Considered Options

* fuzzy-assertions, with a generated test per run
* A judge built into the harness

## Decision Outcome

Chosen option: "A judge built into the harness".

* The judge is an isolated model call that returns a pass or fail for each assertion, with the evidence it relied on.
* A judge error is reported as an error, never counted as a pass.
* Verdicts are saved next to the transcripts, in a form test tools can read.

Against fuzzy-assertions on the same 17 transcripts, 29 of 34 verdicts agreed, and every disagreement was on one ambiguous assertion, which was then reworded to say what it meant.

### Consequences

* Good, because running, judging and reporting are one step.
* Good, because a judge's failure can't hide as a pass.
* Bad, because Tapestry now maintains its own judge.

## Pros and Cons of the Options

### fuzzy-assertions

* Good, because it is an existing, isolated judge.
* Bad, because it needed a generated test per run and a separate step to turn verdicts into pass rates.
* TODO: record any other reason it was replaced.

## More Information

Written on 2026-10-14, after the fact.
