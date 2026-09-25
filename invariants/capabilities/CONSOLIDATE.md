# Consolidate

The mind's sleep. It runs occasionally in the background and does two things:
- turns recent observations into lasting [memories](../primitives/MEMORY.md): what was learned, and who and what it concerns;
- strengthens [associations](../primitives/ASSOCIATION.md) between memories that proved useful together, while unused ones weaken.

Deciding what an episode taught takes judgment, so an agent does that part. Consolidation only ever adds to the mind or adjusts association strengths. It never rewrites what's there.

## Invariants

- **Consolidation never edits a memory.** What it learns is added as new memories, alongside the observations it came from, which stay recallable exactly as they were.
- Every memory it creates is supported by the observations it was drawn from. A memory that claims more than its sources say is not added.
- Specifics survive. Names, numbers and dates in the new memory match the observations exactly.
- It builds only on observations, never on its own earlier inferences, so mistakes can't compound across rounds.
- A new memory goes into the narrowest [scope](../primitives/SCOPE.md) where it's true. If it concerns the owner rather than one project, it goes into the wider scope, and that placement is recorded as evidence.
- New memories start no more confident than their sources justify.
- Association strength changes only with actual use: strengthened when memories were useful together, weakened by time. Being recalled together isn't enough on its own.
- Consolidation runs when there's enough new material and the agent is idle. It never runs mid-conversation, and it never runs continuously.
- Running it twice over the same material changes nothing the second time.
