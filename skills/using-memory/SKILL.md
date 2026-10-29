---
name: using-memory
description: How to use tapestry, your long-term memory, well. Use when deciding whether to note something, which topic a memory belongs in, whether to search a topic that isn't open, when to create or describe a topic, or how to handle recalled memories that are old, weakly matched, brought to mind by another memory, or in conflict.
---

# Using tapestry

Tapestry is your long-term memory. It keeps facts, not conversations: what is true, decided or preferred, and worth knowing later. If your host has a memory of its own it still works and tapestry records what you write there, but a memory kept only there carries no source or topic, so prefer `tapestry_note`. Its guide is already in your context; this skill adds the reasoning and examples behind it.

## What happens without you

- **Recall is automatic.** At the start of a session you're shown where things stand in its place. Before each prompt, memories from the general topic and the open topics are recalled and shown to you with labels.
- **Remembering is automatic.** The conversation is kept for a while as an episode. After it ends, a model the owner chose draws the lasting facts it taught, files each where it matters, and links it to what it builds on. Then the conversation is let go. A conversation that taught nothing leaves nothing behind, and that's fine.

So most of the time you do nothing. Your job is the judgment the automatic parts can't make.

## When to call tapestry_note

Note a fact when it shouldn't wait for the conversation to end, or be left to someone else's reading of it.

**1. The user asks you to remember something.** Do it now, in their words.

> "Remember that the staging database is reset every Sunday."
> `tapestry_note(source="user", content="The atlas staging database is reset every Sunday.", volatility="stable")`

**2. The user corrects something you recalled.** Supersede it, so the old one stops being recalled.

> You recalled `#212 Atlas deploys from main`. The user says: "No, we deploy from release now."
> `tapestry_note(source="user", content="Atlas deploys from the release branch.", supersedes=212, volatility="project")`

**3. You worked something out that's worth keeping.** A conclusion, a diagnosis or a decision you reached.

> After debugging, you find the gateway crashes whenever the log disk fills.
> `tapestry_note(source="agent", content="The atlas gateway crashes when its log disk fills; log rotation prevents it.", builds_on=[87])`

`builds_on` names recalled memories the note adds to, so recalling one brings the other to mind. Saying "this replaces…" or "see #87" in the text does neither.

**Don't note** ordinary conversation, a question, an instruction for this session, or something you'd only be restating. If it's worth keeping, it will be drawn from the conversation.

## Choosing topics

- **General** (pass `topics=[]`): what matters in every context. "Sam prefers short, direct answers."
- **A topic**: what only matters for one person, subject or project. "Atlas deploys to Fly.io" belongs in `atlas`. "The grocery budget is $600" belongs in `finance`.
- **Several**: a memory about more than one thing. "Sam's salary is $105,000" can go in `["sam", "finance"]`. It's visible wherever any of them is searched. Keep sensitive memories narrow: they show up in every topic they're filed under.
- **Omitted**: filed where this session happens, its project. Working in atlas, a fact about Sam's salary still belongs in `finance`, so say so.

## Forgetting

When the user asks you to forget something, find its `#number` with `tapestry_recall`, then call `tapestry_forget(memory=N)` without `confirmed`. It forgets nothing and tells you what would go. Say that to the user and ask. Only after they agree, call it again with `confirmed=true`. Forgetting is complete and can't be undone: the memory, its evidence and the words it came from in any conversation still kept. If the memory had corrected an older one, the older one is current again: tell the user, so they can ask you to forget that one too.

## Searching topics that aren't open

Everything in an unopened topic is invisible until you ask for it. You can always see the list of topics and their descriptions.

- Before telling the user you don't know something, check whether any topic's description fits the question. If one does, search it: `tapestry_recall(query=..., topics=["finance"])`.
- If a topic will come up repeatedly this session, open it: `tapestry_topics(action="open", topic="finance")`.
- An empty result from a search that did run means nothing related is stored. Say so plainly, and don't guess.

## Creating topics

Create one when a new person, subject or project keeps coming up and doesn't fit an existing one: `tapestry_topics(action="describe", topic="home-assistant", description="The house automation setup: devices, automations and fixes.")`

Write the description for your future self, who will be deciding from it alone whether to look inside. Name what's in it, not just the subject.

## Reading recalled memories

- **Match is not truth.** `[match: ok]` means on topic. `[match: LOW]` or `keyword only` means it may not answer the question at all.
- **Brought to mind.** `[brought to mind by #N]` means the memory wasn't matched to the moment. It's linked to #N, which was. Treat it as context, not as an answer.
- **Belief is a separate label.** `belief: likely (0.78), the user said it, last checked 5 months ago, preference` says how likely the memory is still right and why. It fades with time at a rate set by how fast that kind of thing changes: a name barely, a preference slowly, a plan within weeks. A name from 2024 is fine. An address or a preference from 2024 is worth confirming before anything costly depends on it: shipping, paying, deleting.
- **Record the answer.** When you ask the user whether something is still true, record their reply with `tapestry_record_answer`. If it changed, also save the new value with `tapestry_note`, passing the old memory as `supersedes`. Your own use or repetition of a memory never counts as evidence.
- **Say how fast it changes.** When you note something, pass `volatility`: `immutable` for a name, `stable`, `preference`, `project` for a plan, `transient` for what you're in the middle of.
- **Conflicts.** Two current memories that disagree are unresolved: a correction would have superseded the old one. Say so and show both, check `tapestry_why`, or ask the user. Don't quietly pick one.
- **Provenance.** `tapestry_why` on a `#number` shows who said it and when. Use it before relying on a memory for something that matters.
