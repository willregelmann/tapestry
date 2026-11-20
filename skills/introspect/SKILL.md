---
name: introspect
description: Looks over your tapestry memory as a whole and tidies it, covering topic boundaries and descriptions, memories filed in the wrong place, repeats, conflicts, stale or doubted memories, and patterns never written down. Use when the user asks you to introspect, audit, reflect on, review, tidy or reorganise your memory or its topics.
argument-hint: "[what to look at, e.g. topics, or one topic's name]"
---

# Introspect

Look at your memory as a whole, decide what is worth fixing, fix what is safely yours to fix, and bring the rest to the user. `$ARGUMENTS` narrows it when given: a section (`topics`, `memories`, `web`, `working`) or one topic's name.

`tapestry_introspect` does the finding by counting, with no model behind it. Everything it lists is a place to look, not a finding: two memories "alike" may say different things, and a memory "filed apart" may be exactly where it belongs. The judgment is yours, and it is the only expensive part, so spend it where it changes something.

## Keeping it cheap

- Start with `tapestry_introspect()` alone. It returns only how much each section found. Pick the one or two sections with the most to gain and say which you are leaving.
- Ask for one section at a time, at the default limit. Raise the limit or narrow to a `topic` only when the first page was worth it.
- What a listing shows of a memory is enough to decide most things. Call `tapestry_why` or `tapestry_recall` only when a decision turns on words you weren't shown.
- Change things in bulk: `tapestry_file` takes many memories in one call.
- Stop when a page turns up nothing worth changing. A pass that fixes the ten things that matter beats one that reads everything.

## What to look at, in order of what it buys

**1. Topics.** A topic is a boundary on what gets searched, not a category, so the test for every boundary is whether a session would want one side without the other.

- *Descriptions.* You already have every top-level topic's description in context. One that says where a thing is, or only repeats its name, doesn't say what it holds. For those, `tapestry_introspect(section="topics", topic=NAME)` returns its most typical memories; rewrite the description from them with `tapestry_topics(action="describe")`. Say what the topic is not when a neighbour could be confused with it.
- *Topics that hold much the same, or almost nothing.* Usually one is a part of the other, or they are one subject. Propose moving it under the other, or onto it. Leave a small topic alone when it is wanted on its own, or is where a project's sessions happen.
- *Crowded topics.* Size alone is no reason to split. Split only when you can name a part that is wanted without the rest.
- *Memories filed apart from what they resemble.* Check each against the test above, then refile the ones that are misplaced. One in a topic above the topic it belongs to is invisible from below. One in the general topic that matters only somewhere shows up in every session.

**2. Memories.**

- *Say the same.* When one adds nothing to the other, note a single memory that says it properly and supersedes one, and tell the user the other is a candidate to forget. When they differ in a detail, they are not the same: leave them.
- *In conflict.* When one plainly replaces the other, such as a newer value or a later decision, supersede the old one with `tapestry_note(supersedes=…)`. When you can't tell which is right, ask the user, a few at most.
- *Doubtful.* Mostly old accounts of what was happening at the time. They are candidates to forget, never to rewrite.
- *Long.* A memory that states several things matches loosely and repeats others. Leave it unless it is causing one of the problems above.

**3. The web.** Memories alike but never associated, and things said in several topics but never as one general fact.

- *A pattern.* Note it once: `source="agent"`, `builds_on` the memories it rests on, filed in the general topic only when it matters everywhere. Don't write a pattern you would not act on.
- *Two memories that belong together.* When there is something to say about how they relate, note that, with `builds_on` both. When there is nothing to add, link them with `tapestry_associate` and a reason. Memories that say similar things are already found together, so most of the "alike" list needs nothing: link what a search for one would never reach the other by. Weaken a link only when it keeps bringing the wrong memory to mind.

**4. Working memory.** Anything kept a long time with no end: remove it if it is done, or give it an end.

## What is yours to do, and what waits

| Do it, then report it | Show the user and wait |
|---|---|
| Rewrite a topic's description | Move a topic, or merge one into another |
| Refile a memory | Forget anything |
| Supersede a memory that is plainly out of date | Anything in a topic that looks private |
| Note a pattern, or link two memories, with a reason | Choosing between two memories when you can't tell which is right |
| Remove finished working memory | |

Moving and forgetting refuse to act until confirmed, and their first call says what would change: show that to the user. Collect what needs the user and ask once, at the end, not one at a time.

Give a short `reason` with each change: it is kept with the change. Everything in the left column except superseding is recorded and can be undone, one change at a time or the whole session at once, with `tapestry_changes`. Tell the user that at the end, with the session to name.

Never touch a memory's words. There is no editing one, only superseding it with a new one that carries its own source.

## Report

Say what you looked at and what you left, what you changed, by kind and with counts, and what you propose, each as a one-line choice the user can accept or decline. Keep it short: the user wants a mind that works better, not a tour of it.
