# Consolidate

The mind's sleep, and the automatic path into memory. It runs occasionally in the background and does two things:
- draws lasting [memories](../primitives/MEMORY.md) from recent [episodes](../primitives/EPISODE.md): what the conversation or the reading taught, who and what it concerns, where it matters, and which memories it builds on or corrects;
- strengthens [associations](../primitives/ASSOCIATION.md) between memories that were in mind together when a moment taught something new, while unused ones weaken.

Deciding what an episode taught, and what each fact builds on, takes judgment, so a model does that part, in the background. What a drawn memory may claim is fixed by what a memory is, and how it is linked is fixed by what an association is. It runs as a background [job](../interfaces/JOBS.md).

## Invariants

- Consolidation only adds memories, evidence and associations, and adjusts association strengths. It never rewrites or deletes a memory, and never alters an episode. A correction it finds is proposed as a new memory that supersedes the old one, through the same checks as any other.
- **A conversation that taught nothing adds nothing.** Questions, greetings, instructions and replies are not drawn into memories.
- **What a conversation leaves is strictly limited by its length.** Drawing keeps its gist, as people do, never a record of it: when a conversation taught more than the limit allows, what is most worth knowing later is kept.
- **A reading is drawn in full, not for its gist.** The agent chose it to learn from, so it leaves everything it says that is worth knowing later about what it was read for, and a long source leaves as much for its length as a short one. What it says that is off that subject is not drawn.
- What a reading says is its source's claim, filed in the topic it was read for. It is never the user's word, and never the agent's.
- **A reading never supersedes what the mind holds.** Being read later doesn't make a source right: when two sources disagree, each keeps its claim, and both are current.
- It doesn't add a fact the mind already holds. It adds the conversation as evidence for it, from whoever said it.
- The manual path never waits for it. A fact the agent noted in conversation is in memory at once, and consolidation treats it as a fact the mind already holds.
