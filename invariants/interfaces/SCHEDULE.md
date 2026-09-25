# Schedule

What starts Tapestry's background work. [Consolidate](../capabilities/CONSOLIDATE.md), [Calibrate](../capabilities/CALIBRATE.md) and deferred [Revise](../capabilities/REVISE.md) judgments don't run on any host's schedule. Tapestry keeps its own, because hosts differ: some have a scheduler, some have no notion of "idle".

| Trigger | Starts |
|---|---|
| A session ends and enough new material has built up | Consolidate |
| Judgments are waiting for the [reasoner](REASONER.md) | Revise |
| Enough new answers from the user have come in | Calibrate |

## Invariants

- Background work never runs during a conversation in the same mind, and never continuously.
- Background work runs on the owner's machine. The only thing it reaches outside is the reasoner.
- A run that fails partway says so. The next session is told that background work is behind, rather than being shown results as if they were current.
- Running background work twice over the same material changes nothing the second time.
- Background work never asks the user anything. Questions wait for the next conversation, where [Verify](../capabilities/VERIFY.md) decides whether they're worth asking.
