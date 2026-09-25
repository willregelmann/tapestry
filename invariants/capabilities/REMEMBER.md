# Remember

Records what just happened (what was said, done or returned by a tool) as observations in the agent's [mind](../primitives/MIND.md), word for word, each with [evidence](../primitives/EVIDENCE.md) naming its source.

Remembering involves no judgment. What an observation *means* is worked out later, by [Revise](REVISE.md) and [Consolidate](CONSOLIDATE.md). Recording has to be fast, faithful and cheap enough to run on every turn.

## Invariants

- Observations are recorded as they happened. Nothing is summarized, reworded or interpreted on the way in.
- Every observation names who or what it came from. Words the user pasted from someone else are attributed to that someone, not to the user.
- An observation lands in the [scope](../primitives/SCOPE.md) of the session where it happened.
- Remembering is idempotent. Recording the same moment twice produces one observation.
- **A new observation can be recalled right away**, without waiting for any background work.
- If recording fails, the agent is told. A turn is never silently left unremembered.
