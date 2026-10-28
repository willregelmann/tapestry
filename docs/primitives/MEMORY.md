# Memory

A fact: one claim about something, standing on its own, such as "Sam keeps bees in Vermont" or "Atlas runs its tests with pytest". A memory is never a message, a question, an instruction or a reply. What was said in a conversation is an [episode](EPISODE.md), and a memory is what the conversation taught. A fact comes to be in one of two ways: the agent notes it in conversation, or a model draws it from an episode afterwards. Either way it might be stated by someone, or inferred from what was said. These are all the same kind of thing: what separates them is where the claim came from and how it's connected, not a declared type.

A memory has two halves that behave in opposite ways. Its content is fixed the moment it exists. Its [evidence](EVIDENCE.md) only ever grows. How much it's believed is never kept on the memory: it's read off the evidence, at a moment, which is why belief fades when nothing renews it and why it can always be explained.

Deciding what a conversation taught, and whether something new confirms, contradicts or replaces a memory, takes judgment, so a model makes that call: the agent in conversation, or a model working in the background. The model only proposes. Its call goes through the same checks as any other evidence, and how belief moves once the call is made is fixed.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Current : noted by the agent, or drawn from an episode
    Current --> Superseded : a correction or a new value
    Current --> [*] : forgotten, only on an explicit request
    Superseded --> [*] : forgotten, only on an explicit request
    note left of Current
        Content never changes.
        Belief moves with evidence and fades with time.
    end note
    note right of Superseded
        Kept in history, never shown as current
    end note
```

## Invariants

- **A memory is a fact, never a message.** It says one thing, in words that make sense without the conversation it came from. A question, an instruction, a greeting or a reply is not a memory.
- **A memory's content never changes.** A correction is a new memory that supersedes the old one. When the user states a new value for something that holds one value at a time ("my favorite color is blue now"), the old memory is superseded, not merely doubted. A superseded memory stays in the mind's history, marked as superseded.
- **Belief is read from evidence, never stored or granted.** Replaying a memory's evidence gives its confidence at any moment. The same rules weigh evidence whatever produced it. Changing how much a kind of source is trusted changes future readings, never the evidence.
- Confidence answers two questions: *was this right when it was last checked*, and *is it still right now*. Nothing renews itself. Without fresh evidence, confidence fades at a rate set by how quickly that kind of thing changes. A name barely fades, a preference slowly does, and a plan expires. What a session was in the middle of is the fastest kind: it fades within days, and finishing the work supersedes it. It is recalled only when a moment bears on it.
- **Only what happens outside the mind moves belief.** Recalling, repeating or relying on a memory is never evidence for it. Belief is hard to move and hard to fake: a single weak contradiction doesn't topple a long record of confirmations, and the same source repeating itself counts once.
- A memory is attributed to whoever the claim came from, not to whoever wrote it down. A fact the user stated is "the user said it" even when the agent noted it, and words the user pasted from someone else belong to that someone.
- A memory drawn from an episode claims no more than the words say, keeps their names, numbers and dates exactly, and starts no more confident than its source justifies. Its content is drawn from what was said, never from other memories, so mistakes can't compound. It can be associated with the memories it builds on, without borrowing their words.
- Noting the same fact from the same moment twice gives one memory, and a fact that can't be recorded is reported, never silently lost.
- **A memory ends only when its owner asks to forget it.** Fading, superseding and inferring never delete. A memory nothing reaches fades from recall but is never destroyed. Forgetting is complete: the memory, its evidence, its associations, any memory drawn from it alone, and the words it came from in every episode. Afterward the mind can say that something was forgotten, never what.
