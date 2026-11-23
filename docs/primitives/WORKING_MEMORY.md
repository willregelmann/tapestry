# Working Memory

Something the agent is keeping in front of itself for now: today's to-do list, what it is waiting on, where a piece of work was left. It belongs to one [topic](TOPIC.md), or to the general topic, and is shown whenever that topic is searched, without being looked for.

It is the opposite of a [memory](MEMORY.md) in every way that matters. A memory is a claim, believed as far as its evidence goes, found when it bears on the moment and never edited. A working memory is a note to self: it claims nothing, it is always in view, and the agent rewrites it or throws it away as the work moves.

Deciding what is worth keeping in view, and for how long, takes judgment, so the agent makes that call. Where one is shown and when it is gone are fixed.

## Invariants

- **A working memory is shown whenever its topic is searched, and nowhere else.** One in the general topic is in view in every session. One in `atlas` comes into view when a session starts with `atlas` or a topic above it open, the moment one of them is opened, and when a lookup names one, and otherwise behaves as if it doesn't exist.
- **It is only ever written deliberately.** Nothing is drawn into working memory from a conversation, and nothing in the background writes, rewrites or removes one before its end.
- The agent, or the owner in the viewer, can write one, rewrite it and remove it at will. Rewriting replaces what it says, and nothing of what it said before is kept.
- **It is never a memory.** It has no evidence and no belief, it is in no association, recall never returns it as something remembered, and it never becomes a memory by itself. What it says is remembered only if it is also said in a conversation, or noted.
- It can have an end, given when it is written. Once its end has passed it is gone: never shown again, and nothing of it kept. Rewriting it leaves its end where it was, unless the rewrite gives a new one.
- One with no end stays until it is removed.
- Wherever it is shown, it says how long ago it was last written, so a stale one can be seen for what it is.
- It is for what has to outlast the session it is written in. What matters only until a session ends is the session's own business.
- Working memory that can't be read says so. An agent is never shown none because none could be read.
