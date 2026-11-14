# Jobs

What runs in the background, outside any conversation. [Consolidate](../capabilities/CONSOLIDATE.md) and judgments about [memories](../primitives/MEMORY.md) that had to wait are background jobs, and they don't run on any host's terms. Hosts differ here: some have a scheduler, some have no notion of "idle". So Tapestry starts its own jobs, when a session ends through the [hooks](HOOKS.md) and from a timer of its own.

| Job | Starts when |
|---|---|
| Consolidate | A session ends and enough new material has built up |
| The waiting judgments | Judgments are waiting |
| Old episodes go | A session ends and some [episodes](../primitives/EPISODE.md) have outlasted their time |

## Invariants

- **A job never draws from a conversation that is still going, and never holds up one that is.** A conversation in progress, in any session, carries on as if no job were running.
- A job never runs continuously. Each run does a strictly limited amount of work, and only one runs at a time.
- Jobs run on the owner's machine. The only thing they reach outside is a model the owner chose to make their judgments, and it is sent no more than its question needs. With no model chosen, nothing leaves the machine, and the next session is told what is waiting. The choice is the owner's as it stands when a job runs: once they withdraw it, no job sends anything, whatever was chosen when its session began.
- A job that fails partway says so. The next session is told that background work is behind, rather than being shown results as if they were current.
- Running a job twice over the same material changes nothing the second time.
- A job never asks the user anything. Questions wait for the next conversation, where [Verify](../capabilities/VERIFY.md) decides whether they're worth asking.
