# Associations are unlabeled and directed, recall spreads along them, and use decides their strength

## Context and Problem Statement

Some of what an agent should have in mind shares no words with the moment: a question about Thursday should bring the trip to mind. Search alone finds what matches. Something has to carry recall from what matches to what it brings to mind. What connects memories, what does a connection mean, and what makes one stronger?

## Decision Drivers

* Recall reaches memories that are relevant without matching.
* Meaning lives in memories, where it can be believed, superseded and explained, not in a second vocabulary on the connections.
* No memory comes to dominate recall just by being popular.
* How strongly one memory leads to another never changes how much either is believed.

## Considered Options

* Unlabeled, directed, weighted associations, with recall spreading along them
* TODO: record whether labeled edges, as in knowledge-graph memories like Zep/Graphiti, were weighed, and why they were turned down

## Decision Outcome

Chosen option: "Unlabeled, directed, weighted associations, with recall spreading along them".

* **[Associations](../primitives/ASSOCIATION.md) carry no labels.** There is one kind. A relationship worth stating is itself a memory.
* **Direction matters.** How strongly A brings B to mind is independent of how strongly B brings A.
* **Strength comes from being useful together**, and weakens with time. Being recalled together isn't enough.
* **[Recall](../capabilities/RECALL.md) spreads outward** from the memories a lookup reaches directly, and follows an association only when both ends are visible.
* The total strength flowing into any one memory is bounded.

### Consequences

* Good, because recall can surface what is relevant without sharing words with the question.
* Good, because everything that can be believed or wrong is a memory, with evidence.
* Bad, because "useful together" needs a definition the product can observe. It is still an open question in the build plan.
* Bad, because spreading recall costs more than a single search.

## More Information

Written on 2026-10-14, after the fact. Research: HippoRAG 2 (2502.14802) on spreading recall over a memory graph; RAG versus GraphRAG (2502.11371); Turrigiano on synaptic scaling, the model for bounding strength into one memory.
