# Commands

Where an owner manages their [minds](../primitives/MIND.md), outside any conversation. The owner is on the other side: a person at a terminal, not an agent. Everything a mind learns reaches it in conversation, through the agent's [tools](TOOLS.md), or from conversations in the [background](JOBS.md), and the owner looks at what it remembers through the [viewer](VIEWER.md). Commands are for the minds themselves: which exist, which machines serve them, which one a session attaches to, and redoing what the background drew.

## What an owner can do

| Command | Does |
|---|---|
| Create a mind | Makes a new, empty mind with a name the owner gives it |
| List minds | Shows each mind the owner has on this machine, and whether it is behind |
| Attach | Sets which mind sessions on this machine, or in one place, attach to |
| Connect | **Deferred.** Makes a mind on another machine this machine's copy of the same mind, so sessions here and there are one identity |
| Disconnect | **Deferred.** Stops this machine serving a mind. The mind itself is untouched |
| Status | Shows whether a mind is behind, and whether [background work](JOBS.md) is behind |
| Redraw | Takes back what was drawn from chosen [conversations](../primitives/EPISODE.md), or every one still kept, so the background draws them again. Shows what would go and asks first |
| Destroy | Deletes a mind completely, on the owner's explicit confirmation |

## Invariants

- **No session is ever attached to a mind its owner didn't choose.** Attaching is explicit, and a session can always tell which mind it is attached to.
- A host with no choice made gets one default mind, so ordinary use never needs a command.
- Commands never write or rewrite a memory or its evidence. The only ones that take anything away, destroying a mind and redrawing its conversations, say what will go and ask the owner first.
- Redrawing never runs alongside background work on the same mind, so no conversation is taken back halfway through being drawn.
- Disconnecting a machine never deletes a mind. Only destroying does.
- Destroying a mind is complete, the way forgetting a memory is: its memories, episodes, associations and evidence all go. Afterward nothing says what it held.
- A command that can't finish says so, and leaves every mind as it was. A mind is never left half-created or half-attached.
- Commands act only on minds the owner owns.
