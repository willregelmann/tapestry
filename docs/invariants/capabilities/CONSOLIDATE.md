# Consolidate

The mind's sleep. It runs occasionally in the background and does two things:
- infers lasting [memories](../../primitives/MEMORY.md) from recent observations: what was learned, and who and what it concerns;
- strengthens [associations](../../primitives/ASSOCIATION.md) between memories that proved useful together, while unused ones weaken.

Deciding what an episode taught takes judgment, so the [reasoner](../interfaces/REASONER.md) does that part. What an inferred memory may claim is fixed by what a memory is. When it runs is set by the [schedule](../interfaces/SCHEDULE.md).

## Invariants

- Consolidation only adds memories and adjusts association strengths. It never rewrites, supersedes or deletes anything.
- Association strength changes only with actual use: strengthened when memories were useful together, weakened by time. Being recalled together isn't enough on its own.
