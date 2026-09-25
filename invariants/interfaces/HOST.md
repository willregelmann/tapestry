# Host

The runtime Tapestry lives inside: Hermes Agent, Claude Code or the Claude Agent SDK. The host runs the conversation. Tapestry remembers it. Every host is served by the same core through a thin adapter that maps the host's own events onto a fixed set of moments.

## Moments

| Moment | Tapestry does | Hermes | Claude Code / Agent SDK |
|---|---|---|---|
| Session starts | Opens the owner's [mind](../primitives/MIND.md), loads the session's [scopes](../primitives/SCOPE.md), gives standing instructions | Provider start-up | Session-start hook |
| Before a turn | [Recall](../capabilities/RECALL.md), and puts the results into context | Prefetch | Prompt-submit hook |
| After a turn | [Remember](../capabilities/REMEMBER.md) the turn | Turn sync | Stop hook, reading the new part of the transcript |
| Before context is compacted | Remember anything about to be lost | Pre-compress | Pre-compact hook |
| Session ends | Remember, and possibly start background work | Session end | Session-end hook |
| The agent acts on its own | [Tools](AGENT.md) | Provider tools | MCP server |
| The host's built-in memory is written | Records the write as [evidence](../primitives/EVIDENCE.md) | Memory-write notification | Writes to its memory files |

## Who owns the mind

Each adapter tells Tapestry whose mind a session belongs to, and which scopes to load:

- **Hermes:** the agent's profile (Ash, Wren) owns the mind. Project scopes are optional.
- **Claude Code and the Agent SDK:** the user owns the mind. The session loads the user-wide scope plus the scope of the current project.

Subagents use their parent's mind.

## Invariants

- One core, many adapters. Adapters translate between the host and Tapestry and contain no memory logic. A behaviour that differs by host is a bug.
- The host never reaches a mind's storage directly.
- Host quirks are absorbed by the adapter, never by the core. Examples:
  - a loader that only finds plugins by scanning for certain text;
  - a sanitizer that deletes certain text from memory content;
  - a runtime that quietly swallows errors raised during recall.
- A host missing a moment gets a visible fallback, never silent memory loss.
- The host's built-in memory keeps working. Tapestry records its writes as the agent's own notes: low-weight evidence, never a replacement for what the user said.
- Moving between hosts loses nothing. The same mind can be opened from any host that serves its owner.
