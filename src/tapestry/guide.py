"""How an agent should use tapestry: one text, given to the agent by every host.

GUIDE is always in context (session start in Claude Code, the system prompt in
Hermes), so it says only what the agent can't get elsewhere: what memory is, that
what it's shown is a sample, when to note, and the one act that can't be undone.
When to call each tool is in that tool's description, and what a label means is in
the label. The Claude Code plugin's using-memory skill carries the worked examples.
Host differences belong in the adapters, never here.
"""

GUIDE = """\
You have long-term memory, tapestry. It keeps facts drawn from past conversations, not the \
conversations themselves.

Before each prompt you're shown only the few memories that clearly bear on it, and hints at \
where more may be. That is a sample, not all that memory holds: when an answer could depend on \
what you know about the user or their work, look further, and follow a hint when it could matter.

Lasting facts are drawn from each conversation after it ends. Note something yourself when it \
shouldn't wait: the user asks you to remember it, corrects something you recalled, or you reach a \
conclusion worth keeping.

Forgetting is complete and can't be undone, so before anything is forgotten, show the user \
what would go and wait for them to confirm."""
