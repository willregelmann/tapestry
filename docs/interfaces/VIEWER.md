# Viewer

Where an owner looks at what a [mind](../primitives/MIND.md) remembers, outside any conversation. The owner is on the other side: a person, not an agent. The viewer shows the mind as it stands: its [topics](../primitives/TOPIC.md), the [memories](../primitives/MEMORY.md) filed in them, the [associations](../primitives/ASSOCIATION.md) that lead from one to another, the [evidence](../primitives/EVIDENCE.md) behind each, and what [recall](../capabilities/RECALL.md) would bring to mind for a moment the owner describes. It only shows. Whatever the owner wants changed, they ask the agent, through its [tools](TOOLS.md).

## What it shows

| View | Shows |
|---|---|
| Topics | Every topic with its description and how many memories it holds, and the general topic |
| Memories | The memories in the topics the owner picks, and the associations among them, each with its direction and strength as of now |
| A memory | Its content, how much it's believed and why, its evidence, what it superseded or was superseded by, the topics it's filed in, where its associations lead and what leads to it, and, while its [episode](../primitives/EPISODE.md) is kept, the words it was drawn from |
| Try recall | What recall would bring to mind for a moment the owner writes, searching the topics the owner picks |
| Status | Which mind is shown, and whether it or [background work](JOBS.md) is behind |

## Invariants

- **The viewer never changes a mind.** Looking is never use: opening it, looking through it and trying recall in it add no evidence, strengthen or weaken no association, file nothing, record nothing into any episode, and never upgrade or repair the mind. A mind it can't show without changing it is refused, and the viewer says why.
- It shows only minds the owner owns, only to the owner, and nothing it shows leaves the owner's machine.
- **It labels memories in the same words the agent is given.** Belief is read from the evidence at the moment it's shown, and how well a memory matches is never merged with how much it's believed or described in the same words.
- **Trying recall shows exactly what the agent would have been given:** the same highlights and hints, from the same topics, with the same labels, and, for a memory reached through an association, the memory that brought it to mind.
- A superseded memory is never shown as current. It can be shown, marked as superseded, beside whatever superseded it.
- When two memories that each claim to be current conflict, the viewer shows both.
- An association's strength is never shown as belief, and every memory shows which topics it's filed in.
- An episode appears only as where a memory came from. The viewer never presents one as something remembered.
- A forgotten memory leaves no trace in the viewer beyond the fact that something was forgotten.
- **A viewer that can't read a mind says so.** It never shows an empty mind, or a mind as current, when the mind wasn't read or is behind.
