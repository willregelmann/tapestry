# The agent chooses sources, and Tapestry reads them as episodes drawn in full

## Context and Problem Statement

The owner wants a mind to learn a subject named in words ("learn about the 2026 hurricane season"), from sources the agent finds. Facts reach memory in two ways: the agent notes them, or a model draws them from a conversation afterwards, keeping its gist. Neither was made for a paper or a page the agent sets out to learn from. Who decides what to read, who does the reading, and what does a reading become in the mind?

## Decision Drivers

* A mind that learned a subject answers questions about it from memory, with as little wrong as possible.
* What it learned can be traced to where it came from, as anything else in the mind can.
* The fewest new kinds of thing: a reading should behave like what it most resembles.
* The agent's judgment goes where it's needed, which is choosing what is worth reading.

## Considered Options

* The agent researches and notes facts itself, told how by a skill
* Tapestry follows links from pages chosen to start with
* The agent chooses each source and Tapestry reads it, as a kind of episode drawn in full
* The same, with a reading as a primitive of its own
* The same, with a reading drawn for its gist like a conversation

## Decision Outcome

Chosen option: "The agent chooses each source and Tapestry reads it, as a kind of episode drawn in full", because it learned more for less on both subjects measured, and a reading already behaves as an episode does: it is source material, never recalled, kept for a while, drawn, and the place a fact's evidence points to.

### Consequences

* Good, because the agent spends its turns choosing sources rather than transcribing them, and a mind holding hundreds of facts from what it read answered better than one holding its own few dozen notes.
* Good, because every fact drawn from a reading names its source and when it was read, and is the source's claim, never the user's.
* Good, because forgetting, drawing again, fading and the time an episode is kept apply to readings with nothing new.
* Bad, because a reading leaves many more memories than a conversation, so a mind that reads a lot grows fast, and recall has to find the right few among them.
* Bad, because nothing yet shows that the agent chooses sources well: the questions measured were written from the sources themselves.

### Confirmation

* [Episode](../primitives/EPISODE.md): a reading's words belong to its source; a reading is asked for by the agent, for a source it chose, and is read when asked.
* [Consolidate](../capabilities/CONSOLIDATE.md): a reading is drawn in full, not for its gist; what it says is its source's claim, filed in the topic it was read for.
* [Evidence](../primitives/EVIDENCE.md): a source the agent read, named by where it is found and when it was read.
* [Tools](../interfaces/TOOLS.md): `read`, and the agent chooses what to read while Tapestry does the reading.
* `bench.study`, which compares the two ways of learning on the same subjects.

## Pros and Cons of the Options

### The agent researches and notes facts itself, told how by a skill

Measured as the other arm of `bench.study` (2026-11-26, three runs a subject): 0.66 of questions right on the hurricane season and 0.74 on one paper, against 0.84 and 0.92 for Tapestry reading.

* Good, because nothing new is built: `note` already exists.
* Bad, because the agent keeps a few dozen notes whatever it reads, and its results varied most between runs (0.55 to 0.81 on one subject).
* Bad, because it costs more: the agent's own turns write every fact.

### Tapestry follows links from pages chosen to start with

Tried in `bench.learn` (2026-11-19) on two wikis.

* Good, because the agent need do nothing after choosing where to start.
* Bad, because the most linked unread pages were a site's furniture, such as identifier schemes and statistics pages, not the subject.

### The same, with a reading as a primitive of its own

* Good, because a reading's differences from a conversation would each have a home.
* Bad, because every rule an episode has would be written twice, and the only differences are who is speaking and how much is kept.

### The same, with a reading drawn for its gist like a conversation

Measured in `bench.study`: one paper drawn for what a conversation's drawing gives scored 0.57 and 0.73 in two tries; drawn so that a long source leaves as much for its length as a short one, it scored 0.97.

* Good, because the mind grows no faster than from conversations.
* Bad, because a long source gives the same handful of facts as a short one, and loses to the agent's own notes.

## More Information

The measurements are in PLAN.md (section G, learning from sources) and `bench/results.jsonl` (`kind: study`). How much one drawing call reads is a tuning decision of the build, and for conversations it was measured to make no difference, since their gist budget decides what they leave.
