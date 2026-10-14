# Share

Deliberately passes something one agent remembers to another agent's [mind](../primitives/MIND.md).

**Deferred.** Minds are private, and sharing should be rare. Only these guardrails are settled for now.

## Invariants

- Sharing is deliberate. Nothing crosses between minds on its own, and no mind can be read from outside.
- What's shared arrives as [evidence](../primitives/EVIDENCE.md) *from the sharing agent*. The receiving mind decides how much to believe it. It never inherits the sender's confidence.
- The receiving mind can always tell which agent a shared memory came from.
- Shared memories can be superseded, verified and forgotten like any other memory in the receiving mind. Forgetting one in the receiving mind has no effect on the sender's mind.
