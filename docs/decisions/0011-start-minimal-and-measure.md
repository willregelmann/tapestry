# Start minimal, and nothing ships without a measurement

## Context and Problem Statement

Tapestry makes claims that are easy to state and hard to check: stale memories aren't served as current, nothing leaks from a namespace that wasn't searched, confidence is earned. A memory system can look fine in a demo and still fail at all of these. How does a change prove it helped?

## Decision Drivers

* Every stage has exit criteria a test can check.
* The product's distinctive promises are measured, not only retrieval accuracy.
* Tapestry is compared with what it replaces, not just with itself.
* Behaviour that depends on an agent's judgment is tested with a real agent.
* Start minimal. A design starts with the least structure that could work, and structure is added when a measurement shows it's needed, not in advance.

## Considered Options

* Measure before shipping: a retrieval harness with honesty metrics, plus scenarios with a real agent, judged per run
* TODO: record whether lighter testing, such as unit tests alone, was weighed

## Decision Outcome

Chosen option: "Measure before shipping".

* **A retrieval harness** seeds a mind from fixtures and scores recall accuracy alongside three honesty metrics: stale serves (a superseded memory in the results), namespace leakage (anything served from, or influencing the ranking from, a namespace that wasn't searched), and confident answers to questions that have none.
* **Baselines run on the same fixtures**: the memory system Tapestry replaced, and plain keyword search. A public long-term memory benchmark (LongMemEval) runs alongside the hand-written fixtures.
* **Scenarios** drive a real agent against a seeded mind several times each, and a judge decides whether it behaved as the map requires: reconfirms when it should, doesn't nag, doesn't assert superseded memories.
* **Signal lives only in metadata.** A scenario is hardened until the memory text itself doesn't give the answer away, so a pass means Tapestry's labels did the work.
* A feature that fails its measurement stays behind a flag.
* **Structure is earned the same way.** Typed memories, labeled associations, a memory server and a database server were all left out at the start, to be added if a measurement ever shows the need ([ADR-0004](0004-one-kind-of-memory.md), [ADR-0006](0006-unlabeled-directed-associations.md), [ADR-0008](0008-one-core-thin-host-adapters.md), [ADR-0009](0009-one-file-per-mind.md)).

### Consequences

* Good, because the first core build showed its value in numbers: stale serves fell from 100% to 0, and namespace leakage from 78% to 0, against the system it replaced.
* Good, because changes to the agent's guidance are tested the same way ([ADR-0015](0015-one-guide-for-every-host.md)).
* Bad, because scenario runs are slow and cost model calls, so samples are small.
* Bad, because a judge can be wrong. Its errors are reported as errors, never as passes ([ADR-0012](0012-built-in-scenario-judge.md)).

## More Information

Written on 2026-10-14, after the fact.
