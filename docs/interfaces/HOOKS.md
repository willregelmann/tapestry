# Hooks

Where a host hands Tapestry control. The host is the runtime Tapestry lives inside: Hermes Agent, Claude Code or the Claude Agent SDK. The host runs the conversation, and Tapestry remembers it. Each host has hooks of its own, and a thin adapter maps them onto one fixed set.

## Hooks

| Hook | Tapestry does | Hermes | Claude Code / Agent SDK |
|---|---|---|---|
| Session starts | Opens the owner's [mind](../primitives/MIND.md), opens the session's [namespaces](../primitives/NAMESPACE.md), gives standing instructions | Provider start-up | Session-start hook |
| Before a turn | [Recall](../capabilities/RECALL.md), and puts the results into context | Prefetch | Prompt-submit hook |
| After a turn | Records the turn as [observations](../primitives/MEMORY.md) | Turn sync | Stop hook, reading the new part of the transcript |
| Before context is compacted | Records anything about to be lost | Pre-compress | Pre-compact hook |
| Session ends | Records what is left, and possibly starts background work | Session end | Session-end hook |
| The host's built-in memory is written | Records the write as [evidence](../primitives/EVIDENCE.md) | Memory-write notification | Writes to its memory files |

The agent's [tools](TOOLS.md) are served through each host's own tool surface: provider tools in Hermes, an MCP server in Claude Code and the Agent SDK.

## Background work

[Consolidate](../capabilities/CONSOLIDATE.md), [Calibrate](../capabilities/CALIBRATE.md) and judgments about [memories](../primitives/MEMORY.md) that had to wait run outside any conversation. Hosts differ here: some have a scheduler, some have no notion of "idle". So Tapestry keeps its own schedule, started from the session-end hook and from a timer of its own.

| Trigger | Starts |
|---|---|
| A session ends and enough new material has built up | Consolidate |
| Judgments are waiting | The waiting judgments |
| Enough new answers from the user have come in | Calibrate |

## Who owns the mind

Each adapter tells Tapestry whose mind a session belongs to, and which namespaces to open:

- **Hermes:** the agent's profile (Ash, Wren) owns the mind. It opens no namespaces by default.
- **Claude Code and the Agent SDK:** the user owns the mind. The session opens the namespace of the current project, named after the repository.

Subagents use their parent's mind.

## Invariants

- One core, many adapters. Adapters translate between the host and Tapestry and contain no memory logic. A behaviour that differs by host is a bug.
- The host never reaches a mind's storage directly.
- Host quirks are absorbed by the adapter, never by the core. Examples:
  - a loader that only finds plugins by scanning for certain text;
  - a sanitizer that deletes certain text from memory content;
  - a runtime that quietly swallows errors raised during recall.
- A host missing a hook gets a visible fallback, never silent memory loss.
- The host's built-in memory keeps working. Tapestry records its writes as the agent's own notes: low-weight evidence, never a replacement for what the user said.
- Moving between hosts loses nothing. The same mind can be opened from any host that serves its owner.
- Background work never runs during a conversation in the same mind, and never continuously. It never asks the user anything: questions wait for the next conversation, where [Verify](../capabilities/VERIFY.md) decides whether they're worth asking.
- Background work runs on the owner's machine. The only thing it reaches outside is the model that makes its judgments.
- A background run that fails partway says so. The next session is told that background work is behind, rather than being shown results as if they were current.
- Running background work twice over the same material changes nothing the second time.
