# Recall

Given the current moment in a conversation, brings back what the agent remembers that bears on it. It happens on two paths. Asked by the agent through a [tool](../interfaces/TOOLS.md), it returns what it finds. On the automatic path, before every turn without being asked, it shows only highlights, the few memories that clearly bear on the moment, and hints at where more may be: further matches it found, and topics whose descriptions suggest they bear on it. Context is the agent's to spend, so the automatic path stays small and the agent looks further when it decides to. At the start of a session there is no moment yet, so the moment is the session's place, or the mind as a whole when the session has no place: where things stand, what was decided, what is unfinished, and who the agent is working with. A few [memories](../primitives/MEMORY.md) are reached directly. [Associations](../primitives/ASSOCIATION.md) then carry recall outward to what those memories bring to mind. What it can reach is set by the [topics](../primitives/TOPIC.md) being searched.

The judgment lies with the agent that receives the results, which weighs what came back and decides whether to follow a hint. Recall's job is to make that weighing honest.

## Invariants

- **Automatic recall shows highlights and hints, never everything it found.** Highlights are strictly few, each shown in full with its labels. Before a turn, a loose match is never a highlight, and a moment that nothing clearly matches gets none.
- A hint says where more may be, never what it says, and following it with a lookup shows what it pointed at.
- **A session never starts as a stranger by default.** Before its first turn, recall shows highlights about the session's place, from the general topic and the topics the session opens, and hints at where more is, so the agent starts knowing where things stand and where to look.
- What a session starts with is recalled like anything else, never a separate summary kept beside the mind. It carries the same labels, reflects the same evidence, and is as current as the mind is.
- A memory reached through an association, rather than matched to the moment, says so, and names the memory that brought it to mind.
- **Every result carries two labels that can't be mistaken for each other:**
  - how well it matches the moment;
  - how much it's believed, and why ("you said it, last confirmed five months ago").

  They're never merged into one number or described in the same words.
- **A result never implies more certainty than its memory's evidence supports**, in its wording, its ordering or its labels.
- A memory that is only loosely related is labelled that way. It's never passed off as an answer.
- When two memories that each claim to be current conflict, recall shows both, so the conflict is seen. One never silently wins.
- Recall never presents a superseded memory as current. When the history matters, a superseded memory can be shown, marked as superseded.
- **Recall that fails says it failed.** An agent is never shown "nothing relevant" when the mind wasn't searched.
- Recall fits the agent's budget. When it has to leave things out, it drops the weakest matches first, never the labels on what it keeps.
