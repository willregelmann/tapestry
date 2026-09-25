# Agent

The mind's user in the moment: the model holding the conversation. Recall arrives in its context before each turn. Beyond that, the agent acts on its memory through a small set of tools, which are the same in every [host](HOST.md).

## Tools

Tools are grouped by what they're allowed to do. Exact inputs and outputs belong to the contracts.

**Read** (these never change anything)

| Tool | Returns |
|---|---|
| `recall` | A deliberate search beyond what arrived automatically, with the same labels |
| `why` | The evidence behind a memory: who said it, when, and what has confirmed or contradicted it since |
| `list_scopes` | The mind's scopes, and which are loaded |

**Session**

| Tool | Effect |
|---|---|
| `load_scope` / `unload_scope` | Brings an area of the mind into view for this session, or sets it aside |

**Evidence** (every call carries its source)

| Tool | Effect |
|---|---|
| `note` | Records something deliberately ("remember that…") |
| `record_answer` | Records the user's reply to a reconfirmation |
| `forget` | [Forgets](../capabilities/FORGET.md) a memory completely, after the user confirms |

## Invariants

- The agent reaches its mind only through these tools and the recall that arrives each turn.
- It can reach only its owner's mind, and only the scopes loaded in the session.
- **The agent's use of a memory is never evidence.** Recalling, repeating or relying on a memory doesn't make it more believed.
- The agent is told how to read what it recalls: how well it matches, how much it's believed, and when to consider reconfirming. It is expected to say what a memory does and doesn't establish, rather than asserting it.
- Tool output stays within the agent's budget. It never drops the labels on what it returns.
