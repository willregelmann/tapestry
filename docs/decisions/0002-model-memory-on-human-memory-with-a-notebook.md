# Model memory on human memory, and keep a notebook for accountability

## Context and Problem Statement

Agent memory systems usually aim to be a better database: store everything exactly, retrieve it exactly, and treat forgetting, gist and guessing as defects. But a memory that can't be wrong can't generalize, abstract or guess, and those are the abilities worth having. At the same time an agent has to be accountable for what it claims to remember. What should Tapestry's memory be modelled on?

## Decision Drivers

* The agent can generalize from what it has seen, not just retrieve it.
* Any belief can be traced back to what was said and who said it.
* A departure from a proven mechanism needs a reason, and a measurement.

## Considered Options

* Human memory's mechanisms, with a notebook for accountability
* An exact store: keep everything, retrieve exactly, never fade

## Decision Outcome

Chosen option: "Human memory's mechanisms, with a notebook for accountability".

* **Default to the mechanism human memory uses**: association, fading, consolidation during sleep, keeping the gist. Depart from it only when the pressure that shaped it doesn't apply to an agent, and a measurement shows the departure helps.
* **Accountability comes from a notebook, not from making memory infallible.** People keep notes because they know their memory won't hold. Tapestry keeps [evidence](../primitives/EVIDENCE.md): every belief traces back to its source, and how much it's believed is read from that record.
* **Being wrong is visible and correctable.** Confidence is labelled, stale memories say so, and when the stakes justify it the agent asks.

### Consequences

* Good, because fading, association and consolidation come with a body of research on how and why they work.
* Good, because the notebook gives accountability without freezing memory.
* Bad, because a faded memory can be hard to reach when it suddenly matters. Nothing is deleted, but recall may not find it.
* Bad, because "default to the human mechanism" needs a measurement before any departure, which slows some changes down ([ADR-0011](0011-start-minimal-and-measure.md)).

## Pros and Cons of the Options

### An exact store

* Good, because what was stored is what comes back.
* Bad, because it has no notion of staleness, which research into existing agent memory found to be the best-evidenced failure.
* Bad, because everything ever said competes equally for recall, so the context fills with what doesn't matter.

## More Information

Written on 2026-10-14, after the fact. The research behind it: Schacter, *The Seven Sins of Memory* (1999); Anderson and Schooler (1991) on forgetting as adaptation; McClelland et al. (1995) on complementary learning systems; and a 2026 review of Mem0, Honcho, Zep/Graphiti, Letta, Hindsight and Mnemosyne.
