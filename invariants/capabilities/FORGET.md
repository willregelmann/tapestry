# Forget

Removes a memory entirely when someone explicitly asks. It's the only true deletion in a [mind](../primitives/MIND.md). Everything else fades, or is superseded, and can still be recovered.

## Invariants

- Forgetting happens only on an explicit request. Fading, superseding and consolidation never delete anything.
- **Forgetting is complete.** The memory, its [evidence](../primitives/EVIDENCE.md) and its [associations](../primitives/ASSOCIATION.md) are all removed, and nothing recalls it afterward.
- A memory that was derived only from a forgotten observation is forgotten with it.
- Before deleting anything, the agent confirms what will be forgotten.
- Afterward, the agent can say that something was forgotten, but not what it was.
