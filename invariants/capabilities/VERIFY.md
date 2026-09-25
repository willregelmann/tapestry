# Verify

Decides when a memory is worth checking, and how. The check might be:
- asking the user;
- looking at a tool or file;
- searching the web.

Whatever comes back is recorded as [evidence](../primitives/EVIDENCE.md) and weighed by [Revise](REVISE.md).

This is how a fallible memory stays useful: it doesn't avoid being wrong, it notices. Deciding whether a check is worth it, and how to phrase a question, takes judgment. The limits on how often it checks are fixed.

## Invariants

- A memory is checked only when it's about to be relied on, and only when acting on a wrong memory would cost more than the interruption.
- **The user is not nagged.** Questions to the user are strictly limited per conversation and per week, and a memory that was just checked isn't asked about again soon.
- The phrasing matches the doubt:
  - **Likely, and low stakes:** state the assumption and invite correction: "I'm assuming your favorite color is still pink. Correct me if that's changed."
  - **Genuinely uncertain, or high stakes:** ask directly before acting.
- The user's direct answer outweighs every other source.
- Not getting an answer is never counted as a confirmation. If the user carries on without objecting after an assumption is stated, that is weak evidence at best.
- Occasionally the agent checks a memory it's already confident about, so the mind learns whether its confidence was deserved.
