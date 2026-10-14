# One core, with a thin adapter for each host

## Context and Problem Statement

Tapestry has to serve agents in Hermes Agent, Claude Code and the Claude Agent SDK. The hosts differ in how they load extensions, which moments of a conversation they expose, how they offer tools, and what quirks they have. Where does the memory logic live, and how much of it can differ by host?

## Decision Drivers

* A memory recorded in one host behaves the same in every other.
* A host's quirks never leak into how memory works.
* A mind can move between hosts without losing anything.

## Considered Options

* One core library holding all the logic, with a thin adapter per host
* TODO: record any other shapes that were weighed, such as a memory server every host connects to

## Decision Outcome

Chosen option: "One core library holding all the logic, with a thin adapter per host".

* **The core holds all the memory logic.** Hosts never reach storage directly.
* **An adapter only translates.** It maps the host's own events onto one fixed set of [hooks](../interfaces/HOOKS.md), and serves the [tools](../interfaces/TOOLS.md) through the host's own tool surface. A behaviour that differs by host is a bug.
* **Hermes** loads the core in process as a memory provider. **Claude Code** calls a command-line tool from its hooks and gets the tools from an MCP server. **The Agent SDK** loads the same plugin as Claude Code.
* Host quirks are absorbed by the adapter: a loader that only finds plugins by scanning for certain text, a sanitizer that deletes certain text from memory content, a runtime that swallows errors raised during recall.

### Consequences

* Good, because the core is tested once, and each host gets the same behaviour.
* Good, because a new host needs only an adapter.
* Bad, because Claude Code starts a process for each hook, so the core has to load fast.
* Bad, because each host has its own failure modes, and each adapter needs a contract test against the real host.

### Confirmation

Each host has a test that drives the adapter through the host's own machinery, and the same scenario has to give the same recall in Hermes and in Claude Code.

## More Information

Written on 2026-10-14, after the fact.
