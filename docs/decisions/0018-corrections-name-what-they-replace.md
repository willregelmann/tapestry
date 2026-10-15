# A correction names the memory it replaces

## Context and Problem Statement

Memories are never edited, so a correction is a new memory that supersedes the old one ([ADR-0003](0003-add-never-rewrite.md)). The core supported superseding, but no tool exposed it: an agent could only write a new note. Measured on two live minds, 0 of 603 and 0 of 2,344 memories had been superseded, against 24 and 143 notes whose text announced a replacement. Recall returned both versions. How does an agent retire what a correction replaces?

## Decision Drivers

* A correction retires the memory it corrects, so recall stops returning both.
* What a session can't see, it can't change, or learn exists.
* A mistaken correction saves nothing, rather than retiring the wrong memory.

## Considered Options

* Infer replacement from the note's text
* The note names the memory it supersedes, by its number

## Decision Outcome

Chosen option: "The note names the memory it supersedes".

* Saying "this replaces…" in a note's text retires nothing. Only naming the memory does.
* The memory being superseded has to be visible to the session. A hidden memory and one that doesn't exist get the same refusal, so a refusal never reveals what exists in a namespace the session can't see.
* A memory already superseded can't be superseded again. The refusal names the memory that replaced it.
* Anything that isn't clearly a memory's number is refused, and nothing is saved.

### Consequences

* Good, because corrections now retire what they correct, and the record shows what replaced what.
* Good, because supersession can't be used to probe hidden namespaces.
* Bad, because the agent has to have recalled the old memory first, and has to remember to name it. The guide has to teach that.

### Confirmation

[TOOLS.md](../interfaces/TOOLS.md) lists the option on `note`, and [MEMORY.md](../primitives/MEMORY.md) holds that a correction supersedes.

## Pros and Cons of the Options

### Infer replacement from the text

* Good, because the agent needs no new habit.
* Bad, because a model guessing from wording could retire the wrong memory, silently.
* TODO: record whether this was weighed, or only the explicit form.

## More Information

Written on 2026-10-14, after the fact.
