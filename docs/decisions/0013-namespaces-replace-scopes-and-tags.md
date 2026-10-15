# Namespaces, out of sight until opened, replace scopes and tags

## Context and Problem Statement

Most of what an agent knows doesn't belong in a given conversation. The first build partitioned memory into scopes, with tags beside them. What should decide what a lookup can see?

## Decision Drivers

* What one project, person or topic holds stays out of the way until it's needed.
* Something that matters everywhere is always found.
* A memory can matter in more than one place.
* The agent can tell what exists without seeing it, and choose what to open.
* What isn't searched can't affect what comes back, not even its ranking.

## Considered Options

* Scopes, with tags
* Namespaces: named regions that stay out of sight until a lookup names them or a session opens them

## Decision Outcome

Chosen option: "Namespaces".

* A [namespace](../primitives/NAMESPACE.md) is a person, a topic or a project. The default namespace, for what matters everywhere, is always searched.
* **A memory can belong to several namespaces**, and is visible through any of them.
* **Like a skill, every namespace has a description the agent can always see**, and its contents only when it's opened.
* **A namespace that isn't searched leaves no trace**: its memories don't show up, don't affect ranking and don't count against any limit.
* Filing a memory in a namespace, or taking it out, is recorded as evidence.

### Consequences

* Good, because the agent can find out what it knows about something before deciding to look.
* Good, because one concept replaced two.
* Bad, because the agent has to choose what to open, and can miss a namespace it should have searched. With the guide's help it chose correctly in 3 of 3 runs, which should be rechecked with more namespaces and vaguer descriptions.

## Pros and Cons of the Options

### Scopes, with tags

* TODO: record why scopes and tags fell short.

## More Information

Written on 2026-10-14, after the fact. Where a conversation's own turns are filed is a later decision: [ADR-0016](0016-file-each-turn-in-its-session.md).
