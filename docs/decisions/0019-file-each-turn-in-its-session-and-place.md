# Each turn is filed in its session's namespace and its place's, never the default

## Context and Problem Statement

[ADR-0016](0016-file-each-turn-in-its-session.md) filed every turn in its own session's namespace only. Hermes did that. Claude Code didn't: it filed turns under the session's open namespaces, so with no project they went to the default namespace, the failure ADR-0016 was written to stop, and with a project they went to the project plus every namespace the agent had once opened there. The rule also lived in each adapter, against [ADR-0008](0008-one-core-thin-host-adapters.md). Following ADR-0016 strictly would end something Claude Code users had: a new session in a project recalling what was said there before. Where should a turn be filed so both hosts share one rule?

## Decision Drivers

* A whole turn is a record of what was said, not a claim that matters everywhere.
* Conversations about unrelated things don't recall each other's turns.
* Sessions in the same place, such as one repository, are related, and a later one can find what was said there.
* One rule in the core, for every host.

## Considered Options

* The session's own namespace only (ADR-0016)
* The session's own namespace, and its place's namespace when it has one
* The namespaces open in the session

## Decision Outcome

Chosen option: "The session's own namespace, and its place's namespace when it has one". The owner chose it on 2026-10-14, from Foil's proposal on Tapestry PR #8.

* A turn is filed in `session.<id>` and, when the adapter names a place for the session, in that place's namespace. The place in Claude Code is the project. Hermes sessions have no place, so their turns are filed as before.
* Never the default namespace, and never a namespace only because it's open: opening `finance` once in a project doesn't file every later turn there.
* The rule is in the core. Adapters say what the session and its place are, and file nothing themselves.

### Consequences

* Good, because a later Claude Code session in a project recalls what was said in earlier ones there, without consolidation.
* Good, because Hermes and Claude Code file by one rule, and a session with no place behaves exactly as under ADR-0016.
* Good, because the no-project case in Claude Code stops filing turns in the default namespace.
* Bad, because a long-lived place namespace gathers every turn said there, so recall in it gets more crowded over time, the ADR-0016 problem coming back slowly. Consolidation is what should eventually retire old turns from it.

### Confirmation

[NAMESPACE.md](../primitives/NAMESPACE.md) holds the filing rule, and [HOOKS.md](../interfaces/HOOKS.md) says which hosts give a session a place. Checks: `test_a_turn_is_filed_in_its_session_and_place_never_the_default` (core), `test_claude_code_files_turns_in_session_and_project_only` and `test_hermes_files_turns_in_its_session_only` (both hosts through their real adapters).

## Pros and Cons of the Options

### The session's own namespace only

* Good, because it is the narrowest filing, and nothing crowds any namespace but the session's own.
* Bad, because a new Claude Code session in a project loses what earlier sessions there said, until consolidation exists. In a study of one agent's own noticed recall misses on Hermes (Hearth #41, n = 5, noticed misses only), every one kept for analysis had its answer in another of the agent's own sessions, outside what was searched.

### The namespaces open in the session

* Good, because a turn in a project session would be found from that project.
* Bad, because a session with nothing open, the norm in Hermes and in Claude Code outside a project, falls back to the default.
* Bad, because a namespace opened once for one lookup collects every later turn in that project.

## More Information

The no-project Claude Code fix holds under either of the first two options. The crowding consequence wants a bench fixture with many turns in one place and a single relevant note, where the note must rank first.
