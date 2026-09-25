# Recall

Given the current moment in a conversation, brings back what the agent remembers that bears on it. A few [memories](../primitives/MEMORY.md) are reached directly. [Associations](../primitives/ASSOCIATION.md) then carry recall outward to what those memories bring to mind.

The judgment lies with the agent that receives the results, which weighs what came back. Recall's job is to make that weighing honest.

## Invariants

- **Every result carries two labels that can't be mistaken for each other:**
  - how well it matches the moment;
  - how much it's believed, and why ("you said it, last confirmed five months ago").
- Recall reaches only the [scopes](../primitives/SCOPE.md) the session has loaded. It follows an association only when both ends are visible. Nothing in an unloaded scope affects what comes back.
- Recall never presents a superseded memory as current. When the history matters, a superseded memory can be shown, marked as superseded.
- A memory that is only loosely related is labelled that way. It's never passed off as an answer.
- **Recall that fails says it failed.** An agent is never shown "nothing relevant" when the mind wasn't searched.
- Recalling a memory never changes how much it's believed.
- "Why do you believe this?" is always answerable. Recall can trace any memory back through its evidence, even when the associations to its source have faded.
- Recall fits the agent's budget. When it has to leave things out, it drops the weakest matches first, never the labels on what it keeps.
