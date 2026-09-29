---
name: using-memory
description: How to use tapestry, your long-term memory, well. Use when deciding whether to save something to memory, which namespace a memory belongs in, whether to search a namespace that isn't open, when to create or describe a namespace, or how to handle recalled memories that are old, weakly matched or in conflict.
---

# Using tapestry

Tapestry is your long-term memory. Use it instead of any built-in memory files or memory tools. Its guide is already in your context; this skill adds the reasoning and examples behind it.

## What happens without you

- **Recall is automatic.** Before each prompt, memories from the default namespace and the open namespaces are recalled and shown to you with labels.
- **Remembering is automatic.** Every exchange is saved word for word, filed under the namespaces open in the session. In Claude Code that's the current project's namespace.

So most of the time you do nothing. Your job is the judgment the automatic parts can't make.

## When to call tapestry_note

Only in two cases.

**1. It belongs in a namespace that isn't open.** Automatic memory files it under the open namespaces, so without a note it lands in the wrong place.

> Working in the atlas project, the user says: "My raise came through, I'm on $105,000 now."
> Automatic memory files that under `atlas`, where it doesn't belong.
> Note it: `tapestry_note(content="Sam's salary is $105,000 a year, after a raise in September 2026.", namespaces=["finance"])`

**2. You worked something out that nobody said.** A conclusion, a diagnosis or a decision you reached exists nowhere in the transcript.

> After debugging, you find the gateway crashes whenever the log disk fills.
> Note it: `tapestry_note(content="The gateway crashes when its log disk fills; log rotation prevents it.", namespaces=["atlas"])`

**Don't note** something the user just told you when it belongs in an open namespace, even to phrase it better. It's already saved. A second copy only clutters recall.

## Choosing namespaces

- **Default** (pass `namespaces=[]`): what matters in every context. "Sam prefers short, direct answers."
- **A namespace**: what only matters for one person, topic or project. "Atlas deploys to Fly.io" belongs in `atlas`. "The grocery budget is $600" belongs in `finance`.
- **Several**: a memory about more than one thing. "Sam's salary is $105,000" can go in `["sam", "finance"]`. It's visible wherever any of them is searched. Keep sensitive memories narrow: they show up in every namespace they're filed under.

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
- **Age matters for things that change.** A name from 2024 is fine. An address or a preference from 2024 is worth confirming before anything costly depends on it: shipping, paying, deleting.
- **Conflicts.** If two memories disagree, prefer the newer one and say you're doing so, or ask the user.
- **Provenance.** `tapestry_why` on a `#number` shows who said it and when. Use it before relying on a memory for something that matters.
