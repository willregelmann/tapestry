# Jobs

What runs when nobody is in a conversation. [Consolidate](../capabilities/CONSOLIDATE.md), [Calibrate](../capabilities/CALIBRATE.md) and judgments about [memories](../primitives/MEMORY.md) that had to wait are background jobs, and they don't run on any host's terms. Hosts differ here: some have a scheduler, some have no notion of "idle". So Tapestry starts its own jobs, when a session ends through the [hooks](HOOKS.md) and from a timer of its own.

| Job | Starts when |
|---|---|
| Consolidate | A session ends and enough new material has built up |
| The waiting judgments | Judgments are waiting |
| Calibrate | Enough new answers from the user have come in |

## Invariants

- A job never runs during a conversation in the same mind, and never continuously.
- Jobs run on the owner's machine. The only thing they reach outside is the model that makes their judgments.
- A job that fails partway says so. The next session is told that background work is behind, rather than being shown results as if they were current.
- Running a job twice over the same material changes nothing the second time.
- A job never asks the user anything. Questions wait for the next conversation, where [Verify](../capabilities/VERIFY.md) decides whether they're worth asking.
