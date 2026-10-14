# One guide teaches every host's agent to use its memory

## Context and Problem Statement

An agent that has Tapestry's tools still has to know how to use them: how to read the labels on what it recalls, that remembering is automatic, when a note is worth writing, and how to choose a namespace to search. Without being told, agents duplicated what had just been said and missed namespaces they hadn't opened. How is the agent taught?

## Decision Drivers

* The agent's behaviour is the same in every host.
* The guidance is tested like any other change ([ADR-0011](0011-start-minimal-and-measure.md)).

## Considered Options

* One guide text that every host injects, with a skill for the reasoning and worked examples
* TODO: record whether separate guidance per host was weighed

## Decision Outcome

Chosen option: "One guide text that every host injects, with a skill for the reasoning and worked examples".

* The guide says how to read labels, that remembering is automatic, when a note is worth it (a fact that belongs in an unopened namespace, or a conclusion nobody said), how to choose and search namespaces, and that Tapestry replaces the host's own memory.
* Each host injects the same text through its [hooks](../interfaces/HOOKS.md). Where a host supports skills, a skill adds the reasoning and examples.
* Scenarios give the agent the real tools over a seeded mind, so the guide's effect is measured.

### Consequences

* Good, because guidance changes once for every host.
* Good, because its effect is measured: with the guide, the agent found an unopened namespace and filed a salary under the right one in 3 of 3 runs each, and stopped re-noting what was just said in 2 of 3 runs, up from 0 of 3.
* Bad, because the guide spends some of every session's context.

## More Information

Written on 2026-10-14, after the fact.
