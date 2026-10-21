# Commands

Where an owner manages their [minds](../primitives/MIND.md), outside any conversation. The owner is on the other side: a person at a terminal, not an agent. Everything about what a mind remembers happens in conversation, through the agent's [tools](TOOLS.md). Commands are only for the minds themselves: which exist, which machines serve them, and which one a session attaches to.

## What an owner can do

| Command | Does |
|---|---|
| Create a mind | Makes a new, empty mind with a name the owner gives it |
| List minds | Shows each mind the owner has on this machine, and whether it is behind |
| Attach | Sets which mind sessions on this machine, or in one place, attach to |
| Connect | Makes a mind on another machine this machine's copy of the same mind, so sessions here and there are one identity |
| Disconnect | Stops this machine serving a mind. The mind itself is untouched |
| Status | Shows whether a mind is behind, and whether [background work](JOBS.md) is behind |
| Destroy | Deletes a mind completely, on the owner's explicit confirmation |

## Invariants

- **No session is ever attached to a mind its owner didn't choose.** Attaching is explicit, and a session can always tell which mind it is attached to.
- A host with no choice made gets one default mind, so ordinary use never needs a command.
- Commands manage minds and never edit what a mind remembers. They cannot rewrite a memory or its evidence.
- Disconnecting a machine never deletes a mind. Only destroying does.
- Destroying a mind is complete, the way forgetting a memory is: its memories, associations and evidence all go. Afterward nothing says what it held.
- A command that can't finish says so, and leaves every mind as it was. A mind is never left half-created or half-attached.
- Commands act only on minds the owner owns.
