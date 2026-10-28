# Consolidate

The mind's sleep, and the automatic path into memory. It runs occasionally in the background and does two things:
- draws lasting [memories](../primitives/MEMORY.md) from recent [episodes](../primitives/EPISODE.md): what the conversation taught, who and what it concerns, where it matters, and which memories it builds on or corrects;
- strengthens [associations](../primitives/ASSOCIATION.md) between memories that were in mind together when a moment taught something new, while unused ones weaken.

Deciding what a conversation taught, and what each fact builds on, takes judgment, so a model does that part, in the background. What a drawn memory may claim is fixed by what a memory is, and how it is linked is fixed by what an association is. It runs as a background [job](../interfaces/JOBS.md).

## Invariants

- Consolidation only adds memories, evidence and associations, and adjusts association strengths. It never rewrites or deletes a memory, and never alters an episode. A correction it finds is proposed as a new memory that supersedes the old one, through the same checks as any other.
- **A conversation that taught nothing adds nothing.** Questions, greetings, instructions and replies are not drawn into memories.
- It doesn't add a fact the mind already holds. It adds the conversation as evidence for it, from whoever said it.
- The manual path never waits for it. A fact the agent noted in conversation is in memory at once, and consolidation treats it as a fact the mind already holds.
