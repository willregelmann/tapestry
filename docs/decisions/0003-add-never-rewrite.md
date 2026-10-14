# Add, never rewrite: memories are superseded, and inferences sit beside their sources

## Context and Problem Statement

A memory system has to deal with facts that change and with conclusions drawn from what was said. The common approach is to update a fact in place, and to let a model merge and rewrite memories into tidier ones. How should Tapestry change what it knows?

## Decision Drivers

* What was actually said is never lost or distorted.
* A wrong conclusion can be traced and undone.
* Changing facts don't leave stale versions that look current.

## Considered Options

* Rewrite in place: update facts, and let a model merge and rewrite memories
* Add, never rewrite: supersede what changed, and add inferences beside the observations they came from

## Decision Outcome

Chosen option: "Add, never rewrite".

* **A [memory](../primitives/MEMORY.md)'s content is never edited.** A correction or a new value is a new memory that supersedes the old one. The old one stays in history, marked as superseded, and is never shown as current.
* **[Evidence](../primitives/EVIDENCE.md) is only ever added.**
* **Inferences are added, never substituted.** Consolidation adds what it learned alongside the observations, which stay recallable word for word. It builds only on observations, never on earlier inferences, so mistakes can't compound.
* The only true deletion is an explicit request to forget.

### Consequences

* Good, because specifics survive: names, numbers and dates stay as they were said.
* Good, because any belief can be traced to its sources, and an inference can be dropped without losing what it came from.
* Bad, because the store only grows. Recall has to skip superseded memories, and faded ones still take space.
* Bad, because a fact that reverts (pink, then blue, then pink) becomes a new memory each time.

### Confirmation

The store refuses edits to memory content and evidence, so a build that tried to rewrite would fail rather than drift.

## Pros and Cons of the Options

### Rewrite in place

* Good, because the store stays small and each fact has one current version.
* Bad, because research found that memories degrade under repeated model rewriting, while adding derived facts next to the raw ones does not.
* Bad, because the history of how a belief changed is lost.

## More Information

Written on 2026-10-14, after the fact. Research: arXiv 2605.12978 on memories degrading under repeated rewrites; HaluMem (2511.03506); LongMemEval (2410.10813), which adds derived facts as extra search keys rather than replacing the originals.
