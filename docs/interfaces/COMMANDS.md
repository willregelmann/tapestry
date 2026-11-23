# Commands

Where an owner manages their [minds](../primitives/MIND.md), outside any conversation. The owner is on the other side: a person, not an agent. Everything a mind learns reaches it in conversation, through the agent's [tools](TOOLS.md), or from conversations in the [background](JOBS.md), and the owner looks at and changes what it remembers through the [viewer](VIEWER.md). Commands are for the minds themselves: which exist, which agents hold [keys](../primitives/KEY.md) to them, which one a session attaches to, and redoing what the background drew.

## What an owner can do

| Command | Does |
|---|---|
| Sign in | With an identity the owner already has somewhere else |
| Create a mind | Makes a new, empty mind with a name the owner gives it |
| List minds | Shows each mind the owner has, the agents holding keys to it, and whether it is behind |
| Attach | Sets which mind, among those an agent holds keys to, its sessions on a machine, or in one place, attach to |
| Pair | Approves an agent on a machine that asks for a key to a mind, matching its short code, to read and write or to read only, or declines it |
| Keys | Shows each key's agent, machine and mind, what it may do, and when it was last used |
| Revoke | Cuts a key off. The mind, and what the key wrote, are untouched |
| History | Every change to what the owner's [account](../primitives/ACCOUNT.md) owns, and any reading of a mind by whoever runs Tapestry |
| Status | Shows whether a mind is behind, and whether [background work](JOBS.md) is behind |
| Redraw | Takes back what was drawn from chosen [conversations](../primitives/EPISODE.md), or every one still kept, so the background draws them again. Shows what would go and asks first |
| Destroy | Deletes a mind completely, on the owner's explicit confirmation |

## Invariants

- **No session is ever attached to a mind its owner didn't choose.** Attaching is explicit, and a session can always tell which mind it is attached to.
- A host with no choice made gets one default mind, so ordinary use never needs a command.
- Commands never write or rewrite a memory or its evidence. The only ones that take anything away, destroying a mind and redrawing its conversations, say what will go and ask the owner first.
- Redrawing never runs alongside background work on the same mind, so no conversation is taken back halfway through being drawn.
- Revoking a key never takes anything from a mind. Only destroying does.
- A pairing request lapses unless it's approved within a strictly limited time.
- Commands show only the signed-in owner's minds, and never reveal that other accounts or their minds exist.
- Destroying a mind is complete, the way forgetting a memory is: its memories, episodes, associations and evidence all go. Afterward nothing says what it held.
- A command that can't finish says so, and leaves every mind as it was. A mind is never left half-created or half-attached.
- Commands act only on minds the owner owns.
