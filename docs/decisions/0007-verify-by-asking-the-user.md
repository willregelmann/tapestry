# Verify a memory by asking the user, when the stakes justify it and within strict limits

## Context and Problem Statement

A memory can be stale or misattributed, and the user is the only source who can say for certain whether something about them is still true. Agent memory systems reviewed before Tapestry was built never ask. But an agent that asks too often is a nuisance, and one that never asks acts on stale facts. When should an agent check a memory, and how?

## Decision Drivers

* An agent doesn't act on a stale memory when being wrong would cost more than asking.
* The user is not nagged.
* An answer improves the mind's sense of what it knows, not only the one memory asked about.

## Considered Options

* Never ask: rely on decay and new evidence alone
* Ask when acting on a wrong memory would cost more than the interruption, within strict limits
* TODO: record any other options that were weighed, such as asking on a fixed schedule

## Decision Outcome

Chosen option: "Ask when acting on a wrong memory would cost more than the interruption, within strict limits".

* A memory is checked only when it's about to be relied on and the stakes justify it. See [Verify](../capabilities/VERIFY.md).
* **The phrasing matches the doubt.** A likely memory with low stakes is stated as an assumption the user can correct: "I'm assuming your favorite color is still pink." A doubtful one, or high stakes, is asked about directly.
* Questions are strictly limited per conversation and per week, and a memory that was just checked isn't asked about again soon.
* Silence is never a confirmation.
* Now and then the agent checks a memory it is already confident about, so [Calibrate](../capabilities/CALIBRATE.md) learns whether that confidence was deserved.

### Consequences

* Good, because the user's answer is the strongest evidence there is.
* Good, because the occasional unprompted check keeps calibration honest.
* Bad, because how high the stakes are is itself a judgment, and whether a model rates it per task or it follows from the risk of the tool being used is still an open question in the build plan.

## Pros and Cons of the Options

### Never ask

* Good, because the user is never interrupted.
* Bad, because a stale fact about the user is acted on until something else contradicts it.

## More Information

Written on 2026-10-14, after the fact. Horvitz (CHI 1999) on when an interruption is worth its cost.
