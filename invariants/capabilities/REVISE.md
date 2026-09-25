# Revise

Weighs new [evidence](../primitives/EVIDENCE.md) against what the mind already believes. The evidence might be:
- a confirmation;
- a contradiction;
- a new value replacing an old one ("my favorite color is blue now");
- a memory's "no longer valid when" condition coming true.

Deciding whether something someone says actually confirms, contradicts or replaces a memory takes judgment, so an agent makes that call. How confidence moves once the call is made is fixed and reproducible.

## Invariants

- Confidence moves by the same rules no matter what produced the evidence. Remembering, verifying, sharing and consolidating all feed through Revise.
- A contradiction lowers confidence. A single weak contradiction doesn't topple a memory with a long record of confirmations, and repeated contradictions from the same source count once.
- When the user states a new value for something that holds only one value at a time, the old memory is superseded, not merely doubted. The old memory stays in history.
- A "no longer valid when" condition that appears to have come true is checked before it counts. Once confirmed, it counts as a contradiction.
- A judgment that can't be made in the moment is deferred, not skipped. Until it's made, the memories concerned are presented as possibly out of date.
- The agent's own use or repetition of a memory never counts as confirmation.
