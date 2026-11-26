# A user's confirmation is a note that confirms, and a correction a note that supersedes

## Context and Problem Statement

The agent had a tool of its own, `record_answer`, for the user's reply when it asked whether a memory is still true: confirmed or contradicted. The MVP plan put it out of the product, since a correction is already a note that supersedes. But it also recorded confirmations ("yes, the Speedway address is still where I live"), and a direct answer to a direct question outweighs every other kind of evidence. Where does that answer go once the tool does?

## Decision Drivers

* A user's answer to a direct question keeps its weight.
* Fewer tools for the agent to choose between.
* An unprompted correction is never mistaken for an answer to a question.

## Considered Options

* Fold it into `note`, with one argument covering both a confirmation and a correction
* Fold it into `note`, with an argument for confirmations only, and corrections kept as notes that supersede
* Keep `record_answer`
* Remove it outright, so a confirmation is an ordinary note

## Decision Outcome

Chosen option: "Fold it into `note`, with an argument for confirmations only", because the owner chose folding it into `note` so a confirmation keeps its weight, and the form that covered corrections too was passed for an unprompted correction in every run measured.

### Consequences

* Good, because the agent has one tool for everything that goes into memory, and a confirmation still counts as a direct answer.
* Good, because a correction works as it did: a note that supersedes, with the user's word as its source.
* Bad, because a correction given in answer to a question no longer carries more weight than one the user volunteered. Either supersedes, so what is current is the same.

### Confirmation

* [Tools](../interfaces/TOOLS.md): `note` records the user's answer that a memory they were asked about is still true.
* [Evidence](../primitives/EVIDENCE.md): the user, answering a question, outweighs everything.
* The `record-answer` and `correction-note` scenarios in `bench.scenario`.

## Pros and Cons of the Options

### Fold it into `note`, with one argument covering both a confirmation and a correction

Tried as `answers`: with no content it confirmed, with content it superseded and contradicted with a direct answer's weight. In `correction-note`, where the user volunteers a correction unasked, Sonnet passed it in 5 of 5 runs.

* Good, because one argument covers every reply.
* Bad, because volunteered corrections were given the weight of an answer to a question.

### Fold it into `note`, with an argument for confirmations only

Built as `confirms`. `record-answer`, `correction-note` and `reconfirm-high-stakes` passed 5 of 5 runs each, and no correction passed it.

* Good, because confirming and correcting are different acts with different arguments.
* Bad, because a contradiction with nothing true to say instead has to be noted as a fact that supersedes ("Sam no longer lives in Austin").

### Keep `record_answer`

* Good, because nothing changes.
* Bad, because it is one more tool, and the MVP plan had already put it out.

### Remove it outright

* Good, because it is simplest.
* Bad, because a confirmation would become an ordinary note from the user, with less weight than the map gives a direct answer.
