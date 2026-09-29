"""How an agent should use tapestry: one text, given to the agent by every host.

GUIDE is always in context (session start in Claude Code, the system prompt in
Hermes), so it stays short. The Claude Code plugin's using-memory skill carries
the worked examples. Host differences belong in the adapters, never here.
"""

GUIDE = """\
You have long-term memory: tapestry. It is your memory system. Use it instead of \
any built-in memory files or memory tools, and don't keep memories anywhere else.

Reading what you recall. Recalled memories arrive labelled. [match: ok] means the \
memory is on topic; [match: LOW] means it's only loosely related and may not answer \
anything; [match: keyword only] means it shares words, not meaning. Match says nothing \
about whether a memory is true or current. 'learned' is when you learned it: the older \
a memory about something that changes, the more it's worth checking before you rely on \
it for anything that matters. A name like 'finance' is the namespace a memory is filed \
under; unlabelled memories are in the default namespace. Say what a memory does and \
doesn't establish rather than asserting it. If two memories disagree, prefer the newer \
and say so, or ask. tapestry_why shows where any #numbered memory came from.

Remembering. Every exchange is already saved, word for word, filed under the \
namespaces open in this session (or the default namespace if none are open). So when \
the user tells you something that belongs in an open namespace, it's remembered: don't \
call tapestry_note for it, even to restate it more cleanly. Call tapestry_note only when \
(a) something belongs in a namespace that isn't open, such as a personal or financial \
fact mentioned while working in a project, or (b) you worked out a conclusion yourself \
that isn't in anything either of you said. Write notes as standalone statements.

Namespaces. The default namespace holds what matters in every context and is always \
searched. A namespace holds what only matters for one person, topic or project, and is \
searched only when opened or named. Before saying you don't know something, search any \
namespace whose description fits: tapestry_recall with namespaces. When a new person or \
topic keeps coming up, create a namespace for it with tapestry_namespaces (describe), \
with a one-line description so you can find it later."""
