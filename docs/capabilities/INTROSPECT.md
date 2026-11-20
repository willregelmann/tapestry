# Introspect

Looks at a [mind](../primitives/MIND.md) as a whole instead of searching it: how its [memories](../primitives/MEMORY.md) are spread over [topics](../primitives/TOPIC.md), where they repeat, conflict or are doubted, and where [associations](../primitives/ASSOCIATION.md) are missing between memories that are alike. What it finds, the agent tunes with the same acts it has in any conversation.

Finding where to look takes no judgment: it is counted from what the mind holds. Deciding what a finding means, and what to do about it, takes judgment, so the agent makes that call. What it may do is fixed by what a memory and a topic are. The agent starts it and reads it through its [tools](../interfaces/TOOLS.md).

## Invariants

- **Introspection only looks.** It changes nothing in the mind. Every change that follows is one of the mind's ordinary acts, recorded as it would be at any other time: a [change](../primitives/CHANGE.md) that can be undone, or new evidence.
- **Finding costs no judgment.** No model is asked anything to find what stands out. Judgment is spent only on what was found, and only by the agent that asked.
- **What it lists is a place to look, never a verdict.** Two memories listed as alike may say different things, and a memory listed as filed apart may be where it belongs. Each is said to be a place to look.
- It reads every topic, open or not, so it happens only when it is asked for, and it says what it read.
- Asked about one topic, it lists nothing filed only outside that topic.
- What it shows is strictly limited. It says how much it found before showing any of it, shows only as much of a memory as is needed to recognise it, and says how many more there are.
- Tuning never edits a memory or removes one. A memory that is wrong is superseded, one in the wrong place is filed elsewhere, and forgetting still waits for the user.
- A change that alters what every later session finds, such as moving a topic, is shown to the user and waits for them to agree.
