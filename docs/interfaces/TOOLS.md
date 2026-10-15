# Tools

What the agent holds. The model in conversation reaches its [mind](../primitives/MIND.md) through these tools, which are the same in every host, and through the recall that the [hooks](HOOKS.md) put into its context before each turn.

The user never touches them. Whatever the user wants from their memory, they ask the agent for.

## Tools

Tools are grouped by what they're allowed to do. Exact inputs and outputs belong to the contracts.

**Read** (these never change anything)

| Tool | Returns |
|---|---|
| `recall` | A deliberate search beyond what arrived automatically, with the same labels. It can name namespaces to search alongside the default |
| `why` | The evidence behind a memory: who said it, when, and what has confirmed or contradicted it since |
| `namespaces` | Every namespace with its description, and which are open. Never their contents |

**Session**

| Tool | Effect |
|---|---|
| `open` / `close` | Opens a namespace for every lookup in this session, or closes it |
| `describe` | Creates a namespace, or rewrites its description |

**Evidence** (every call carries its source)

| Tool | Effect |
|---|---|
| `note` | Records something deliberately ("remember that…"), optionally filed under namespaces, and optionally superseding a recalled memory it corrects |
| `record_answer` | Records the user's reply to a reconfirmation |
| `forget` | [Forgets](../primitives/MEMORY.md) a memory completely, after the user confirms |

## Invariants

- The agent reaches its mind only through these tools and the recall that arrives each turn.
- It can reach only its owner's mind, and only the default namespace plus the namespaces it names or opens.
- **The agent's use of a memory is never evidence.** Recalling, repeating or relying on a memory doesn't make it more believed.
- **Whatever the user asks of their memory, the tools can do:** say what's remembered about something, say why it's believed, correct it, or forget it. A correction from the user supersedes the memory and is never argued with.
- The user never has to learn Tapestry or run it. Everything happens through the agent, in ordinary conversation.
- The agent is told how to read what it recalls: how well it matches, how much it's believed, and when to consider reconfirming. It is expected to say what a memory does and doesn't establish, rather than asserting it.
- Tool output stays within the agent's budget. It never drops the labels on what it returns.
