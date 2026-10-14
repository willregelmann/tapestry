# One kind of memory: observations, beliefs and concepts differ by evidence, not by type

## Context and Problem Statement

A mind holds things that look different: what was said word for word, what was inferred from it, and names like a person or a project that other memories gather around. Many memory systems give each its own type, with its own storage and rules. Should Tapestry?

## Decision Drivers

* Each kind of thing has its own rules only if it really is a different kind of thing.
* An observation can become the basis of a belief, and a belief can become a hub, without being converted.
* Fewer types mean fewer places for the rules to disagree.

## Considered Options

* One kind of memory, whose role comes from its evidence and connections
* TODO: record which typed designs were weighed (for example separate observations, facts and entities)

## Decision Outcome

Chosen option: "One kind of memory, whose role comes from its evidence and connections".

* **Every [memory](../primitives/MEMORY.md) is the same kind of thing.** An observation is a memory whose first evidence is an observation. A concept is a memory that many [associations](../primitives/ASSOCIATION.md) lead to. Nothing declares a type.
* What sets them apart is where they came from and how they are connected, so the same rules (never edited, belief read from evidence, superseded rather than changed) hold for all of them.
* A memory as it comes to mind in one moment, with how well it matches and how much it's believed now, is a view of a memory, not a second kind of thing. Its rules belong to [Recall](../capabilities/RECALL.md).

### Consequences

* Good, because one set of rules covers everything the mind holds.
* Good, because a memory's role can change as evidence and connections change, without migration.
* Bad, because questions like "is this an observation?" are answered by looking at evidence, not by reading a field.
* TODO: record any downside that was weighed when this was chosen.

## More Information

Written on 2026-10-14, after the fact. The view of a memory in one moment was considered as a primitive of its own ("recollection") on 2026-10-14 and rejected, because a primitive is a thing the product is made of, not a view of one.
