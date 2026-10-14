# Schedule

What starts Tapestry's background work. [Consolidate](../capabilities/CONSOLIDATE.md), [Calibrate](../capabilities/CALIBRATE.md) and judgments about [memories](../primitives/MEMORY.md) that had to wait run outside any conversation, and not on any host's terms. Hosts differ here: some have a scheduler, some have no notion of "idle". So Tapestry keeps its own schedule, started when a session ends through the [hooks](HOOKS.md) and by a timer of its own.

| Trigger | Starts |
|---|---|
| A session ends and enough new material has built up | Consolidate |
| Judgments are waiting | The waiting judgments |
| Enough new answers from the user have come in | Calibrate |

## Invariants

- Background work never runs during a conversation in the same mind, and never continuously.
- Background work runs on the owner's machine. The only thing it reaches outside is the model that makes its judgments.
- A run that fails partway says so. The next session is told that background work is behind, rather than being shown results as if they were current.
- Running background work twice over the same material changes nothing the second time.
- Background work never asks the user anything. Questions wait for the next conversation, where [Verify](../capabilities/VERIFY.md) decides whether they're worth asking.
