# Consolidate

The mind's sleep. It runs occasionally in the background and does two things:
- draws lasting [memories](../primitives/MEMORY.md) from recent [episodes](../primitives/EPISODE.md): what the conversation taught, who and what it concerns, and where it matters;
- strengthens [associations](../primitives/ASSOCIATION.md) between memories that proved useful together, while unused ones weaken.

Deciding what a conversation taught takes judgment, so a model does that part, in the background. What a drawn memory may claim is fixed by what a memory is. It runs as a background [job](../interfaces/JOBS.md).

## Invariants

- Consolidation only adds memories and evidence and adjusts association strengths. It never rewrites, supersedes or deletes a memory, and never alters an episode.
- **A conversation that taught nothing adds nothing.** Questions, greetings, instructions and replies are not drawn into memories.
- It doesn't add a fact the mind already holds. It adds the conversation as evidence for it, from whoever said it.
- Association strength changes only with actual use: strengthened when memories were useful together, weakened by time. Being recalled together isn't enough on its own.
