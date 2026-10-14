# Each turn is filed in its own session's namespace, not the default

Superseded by ADR-0019.

## Context and Problem Statement

Every conversation's turns are recorded word for word as observations. At first they were filed in the default namespace, which every session searches. On a live mind, 26 of the first 27 memories after the switch were whole turns, one to four thousand characters each, 24 of them from one busy session, and they were recalled into unrelated conversations. Where should a conversation's own turns be filed?

## Decision Drivers

* A whole turn is a record of what was said, not a claim that matters everywhere.
* The conversation it happened in can still find it.
* Claims worth sharing still reach other sessions.
* The list of namespaces in the agent's context doesn't grow a line per conversation.

## Considered Options

* The default namespace
* The namespaces open in the session
* The session's own namespace

## Decision Outcome

Chosen option: "The session's own namespace".

* Each turn is filed in a namespace of its own session. That session searches it automatically. Any other session reaches it only by naming it.
* Notes are unchanged, so the deliberate path still shares across sessions, and consolidation can file what it learns where it matters.
* The agent's list of namespaces shows the current session's and counts the rest.

### Consequences

* Good, because unrelated conversations no longer recall each other's turns.
* Good, because what's shared across sessions is what someone chose to share.
* Bad, because a fact said in passing in one session isn't found from another until a note or consolidation files it more broadly.

### Confirmation

[NAMESPACE.md](../primitives/NAMESPACE.md) holds the filing rule.

## Pros and Cons of the Options

### The default namespace

* Bad, because every session searches it, so whole turns crowded every recall.

### The namespaces open in the session

* Good, because a turn in a project session would be found from that project.
* Bad, because a session with nothing open, which is the norm in Hermes, falls back to the default.
* TODO: record any other reason this was turned down.

## More Information

Written on 2026-10-14, after the fact.
