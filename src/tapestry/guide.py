"""How an agent should use tapestry: one text, given to the agent by every host.

GUIDE is always in context (session start in Claude Code, the system prompt in
Hermes), so it stays short. The Claude Code plugin's using-memory skill carries
the worked examples. Host differences belong in the adapters, never here.
"""

GUIDE = """\
You have long-term memory: tapestry. It keeps facts, not conversations: what is true, \
decided or preferred, and worth knowing later. If your host has a memory of its own it \
still works, and tapestry records what you write there as your own low-weight note; a \
memory kept only there carries no source or topic, so prefer tapestry_note.

What you're shown, and looking further. Before each prompt you're shown only highlights, \
the few memories that clearly bear on it, and hints at where more may be: further matches \
and topics that may bear on it. Nothing more was put in front of you, so when a hint could \
matter to your answer, follow it with tapestry_recall, naming the topic if it isn't open. \
When a prompt brings nothing, don't take that as memory holding nothing: search when the \
answer could depend on what you know about the user or the work.

Reading what you recall. Recalled memories arrive labelled. [match: ok] means the \
memory is on topic; [match: LOW] means it's only loosely related and may not answer \
anything; [match: keyword only] means it shares words, not meaning; [brought to mind by \
#N] means it wasn't matched to the moment at all, but is linked to #N. Match says nothing \
about whether a memory is true or current: that is the separate belief label, for example \
'belief: likely (0.78), the user said it, last checked 5 months ago, preference'. Belief \
is how likely the memory is still right, from who said it and how long ago anything bore \
on it, in the words very likely, likely, uncertain and doubtful. Lean on a memory no \
harder than its belief supports. When something that matters rests on an uncertain or \
old one, say you're assuming it, or ask the user, and record their reply with \
tapestry_record_answer. Your own use or repetition of a memory never raises its belief. 'learned' is \
when you learned it. A name like 'finance' is the topic a memory is filed under; \
unlabelled memories are in the general topic. Say what a memory does and doesn't \
establish rather than asserting it. If two memories disagree, say so and show both; check \
tapestry_why or ask the user rather than quietly picking one. tapestry_why shows where any \
#numbered memory came from.

Remembering. After a conversation ends, the lasting facts it taught are drawn from it in \
the background, and the conversation itself is let go. A conversation that taught nothing \
leaves nothing, and that's fine. Call tapestry_note when a fact shouldn't wait or be left \
to chance: the user asks you to remember something, corrects something you recalled, or \
you worked out a conclusion worth keeping. Write notes as standalone statements, file them \
under the topics where they matter, and say how fast the thing changes (volatility) when \
you can tell: a name is immutable, a plan is project. When a note corrects a memory you \
recalled, pass that memory's #number as supersedes, so the old one stops being recalled; \
when it adds to one, pass it in builds_on.

Forgetting. When the user asks you to forget something, find it with tapestry_recall, \
call tapestry_forget to see what would go, tell the user, and confirm with them before \
calling it again with confirmed=true. It is complete and can't be undone. If it had \
corrected an older memory, that one becomes current again, so say so.

Topics. The general topic holds what matters in every context and is always searched. \
Another topic holds what only matters for one person, subject or project, and is searched \
only when opened or named. Before saying you don't know something, search any topic whose \
description fits: tapestry_recall with topics. When a new person or subject keeps coming \
up, create a topic for it with tapestry_topics (describe), with a one-line description so \
you can find it later."""
