# Viewer

Where an owner looks at what a [mind](../primitives/MIND.md) remembers, and changes it, outside any conversation. The owner is on the other side: a person, not an agent. The viewer shows the mind as it stands: its [topics](../primitives/TOPIC.md), the [memories](../primitives/MEMORY.md) filed in them, the [associations](../primitives/ASSOCIATION.md) that lead from one to another, the [evidence](../primitives/EVIDENCE.md) behind each, and what [recall](../capabilities/RECALL.md) would bring to mind for a moment the owner describes. The owner can also do here anything they could ask an agent to do through its [tools](TOOLS.md): note a fact, correct or forget a memory, file memories, shape topics, link memories, keep working memory and undo changes.

## What it shows

| View | Shows |
|---|---|
| Topics | Every topic that holds something, under the topic it sits in, with its description and how many memories it holds, and the general topic |
| Memories | The memories in the topics the owner picks and under them, and the associations among them, each with its direction and strength as of now |
| A memory | Its content, how much it's believed and why, its evidence, what it superseded or was superseded by, the topics it's filed in, where its associations lead and what leads to it, and, while its [episode](../primitives/EPISODE.md) is kept, the words it was drawn from |
| Working memory | What is being kept in view in the topics the owner picks, with how long ago each was written and when it ends |
| Changes | What the agent has deliberately [changed](../primitives/CHANGE.md) in the mind, newest first: what each did, when, why, and whether it still stands |
| Try recall | What recall would bring to mind for a moment the owner writes, searching the topics the owner picks |
| Status | Which mind is shown, and whether it or [background work](JOBS.md) is behind |

## What the owner can change

| Act | Does |
|---|---|
| Note | Records a fact, from the owner, optionally filed under topics and linked to the memories it builds on |
| Correct | Supersedes a memory with a new one, from the owner |
| Forget | [Forgets](../primitives/MEMORY.md) a memory completely, after the owner sees what will go and confirms |
| File | Files memories in other topics, or takes them out, as a [change](../primitives/CHANGE.md) |
| Topics | Creates, describes or moves a topic, as a change. A move shows what it would change and waits for the owner to agree |
| Associate | Links two memories, or strengthens or weakens what links them, with a reason, as a change |
| Working memory | Writes, rewrites or removes what is kept in view in a topic |
| Undo | Undoes a change, or everything one session changed |

## Invariants

- **Looking is never use.** Opening the viewer, looking through it and trying recall in it add no evidence, strengthen or weaken no association, file nothing, record nothing into any episode, and never upgrade or repair the mind. A mind it can't show without changing it is refused, and the viewer says why.
- **A mind changes in the viewer only when the owner asks it to,** and each act is recorded exactly as the same act through an agent would be, except that it comes from the owner directly: a fact or a correction as [evidence](../primitives/EVIDENCE.md) from the owner, and filing, shaping topics and linking memories as changes that can be undone.
- A change the viewer can't make says so, and leaves the mind as it was. It never shows a change as made when it wasn't.
- It shows only minds the owner owns, only to the owner, and nothing it shows reaches anyone else.
- **It labels memories in the same words the agent is given.** Belief is read from the evidence at the moment it's shown, and how well a memory matches is never merged with how much it's believed or described in the same words.
- **Trying recall shows exactly what the agent would have been given:** the same highlights and hints, from the same topics, with the same labels, and, for a memory reached through an association, the memory that brought it to mind.
- A superseded memory is never shown as current. It can be shown, marked as superseded, beside whatever superseded it.
- When two memories that each claim to be current conflict, the viewer shows both.
- An association's strength is never shown as belief, and every memory shows which topics it's filed in.
- An episode appears only as where a memory came from. The viewer never presents one as something remembered.
- A forgotten memory leaves no trace in the viewer beyond the fact that something was forgotten.
- **A viewer that can't read a mind says so.** It never shows an empty mind, or a mind as current, when the mind wasn't read or is behind.
