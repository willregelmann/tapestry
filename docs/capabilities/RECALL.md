# Recall

Given the current moment in a conversation, brings back what the agent remembers that bears on it. At the start of a session there is no moment yet, so the moment is the question "who am I?", asked of what the mind knows about itself: who the agent is, who it works with, and how it works. A few [memories](../primitives/MEMORY.md) are reached directly. [Associations](../primitives/ASSOCIATION.md) then carry recall outward to what those memories bring to mind. What it can reach is set by the [namespaces](../primitives/NAMESPACE.md) being searched.

The judgment lies with the agent that receives the results, which weighs what came back. Recall's job is to make that weighing honest.

## Invariants

- **A session never starts as a stranger by default.** Before its first turn, recall answers "who am I?" from the default namespace and the namespaces the session opens, so the agent starts with its identity and its place already in mind.
- The answer to "who am I?" is recalled like anything else, never a separate summary kept beside the mind. It carries the same labels, reflects the same evidence, and is as current as the mind is.
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
