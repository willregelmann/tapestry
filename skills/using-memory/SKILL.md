---
name: using-memory
description: How to use tapestry, your long-term memory, well. Use when deciding whether to save something to memory, which namespace a memory belongs in, whether to search a namespace that isn't open, when to create or describe a namespace, or how to handle recalled memories that are old, weakly matched or in conflict.
---

# Using tapestry

Tapestry is your long-term memory. If your host has a memory of its own it still works and tapestry records what you write there, but a memory kept only there carries no source or namespace, so prefer `tapestry_note`. Its guide is already in your context; this skill adds the reasoning and examples behind it.

## What happens without you

- **Recall is automatic.** Before each prompt, memories from the default namespace, the open namespaces and this conversation are recalled and shown to you with labels.
- **Remembering is automatic.** Every exchange is saved word for word, filed under this conversation's own namespace and the namespace of the place it happened in, never the default. In Claude Code the place is the current project, so later sessions in that project find it.

So most of the time you do nothing. Your job is the judgment the automatic parts can't make.

## When to call tapestry_note

Only in two cases.

**1. It matters somewhere other than this place.** Automatic memory files it under this conversation and its project, so without a note it lands in the wrong place, or nowhere useful outside a project.

> Working in the atlas project, the user says: "My raise came through, I'm on $105,000 now."
> Automatic memory files that under `atlas`, where it doesn't belong.
> Note it: `tapestry_note(source="user", content="Sam's salary is $105,000 a year, after a raise in September 2026.", namespaces=["finance"])`

**2. You worked something out that nobody said.** A conclusion, a diagnosis or a decision you reached exists nowhere in the transcript.

> After debugging, you find the gateway crashes whenever the log disk fills.
> Note it: `tapestry_note(source="agent", content="The gateway crashes when its log disk fills; log rotation prevents it.", namespaces=["atlas"])`

**Don't note** something the user just told you when it belongs to this place, even to phrase it better. It's already saved. A second copy only clutters recall.

**Corrections.** When a note corrects a memory you recalled, pass its `#number` as `supersedes`, so the old one stops being recalled. Saying "this replaces…" in the text doesn't do that.

## Choosing namespaces

- **Default** (pass `namespaces=[]`): what matters in every context. "Sam prefers short, direct answers."
- **A namespace**: what only matters for one person, topic or project. "Atlas deploys to Fly.io" belongs in `atlas`. "The grocery budget is $600" belongs in `finance`.
- **Several**: a memory about more than one thing. "Sam's salary is $105,000" can go in `["sam", "finance"]`. It's visible wherever any of them is searched. Keep sensitive memories narrow: they show up in every namespace they're filed under.

## Forgetting

When the user asks you to forget something, find its `#number` with `tapestry_recall`, then call `tapestry_forget(memory=N)` without `confirmed`. It forgets nothing and tells you what would go. Say that to the user and ask. Only after they agree, call it again with `confirmed=true`. Forgetting is complete and can't be undone. If the memory had corrected an older one, the older one is current again: tell the user, so they can ask you to forget that one too.

## Searching namespaces that aren't open

Everything in an unopened namespace is invisible until you ask for it. You can always see the list of namespaces and their descriptions.

- Before telling the user you don't know something, check whether any namespace's description fits the question. If one does, search it: `tapestry_recall(query=..., namespaces=["finance"])`.
- If a topic will come up repeatedly this session, open it: `tapestry_namespaces(action="open", namespace="finance")`.
- An empty result from a search that did run means nothing related is stored. Say so plainly, and don't guess.

## Creating namespaces

Create one when a new person, topic or project keeps coming up and doesn't fit an existing one: `tapestry_namespaces(action="describe", namespace="home-assistant", description="The house automation setup: devices, automations and fixes.")`

Write the description for your future self, who will be deciding from it alone whether to look inside. Name what's in it, not just the topic.

## Reading recalled memories

- **Match is not truth.** `[match: ok]` means on topic. `[match: LOW]` or `keyword only` means it may not answer the question at all.
- **Belief is a separate label.** `belief: likely (0.78), the user said it, last checked 5 months ago, preference` says how likely the memory is still right and why. It fades with time at a rate set by how fast that kind of thing changes: a name barely, a preference slowly, a plan within weeks. A name from 2024 is fine. An address or a preference from 2024 is worth confirming before anything costly depends on it: shipping, paying, deleting.
- **Record the answer.** When you ask the user whether something is still true, record their reply with `tapestry_record_answer`. If it changed, also save the new value with `tapestry_note`, passing the old memory as `supersedes`. Your own use or repetition of a memory never counts as evidence.
- **Say how fast it changes.** When you note something, pass `volatility`: `immutable` for a name, `stable`, `preference`, `project` for a plan, `transient` for what you're in the middle of.
- **Conflicts.** Two current memories that disagree are unresolved: a correction would have superseded the old one. Say so and show both, check `tapestry_why`, or ask the user. Don't quietly pick one.
- **Provenance.** `tapestry_why` on a `#number` shows who said it and when. Use it before relying on a memory for something that matters.
