# Each mind is its own SQLite file, which enforces the never-edited rules itself

## Context and Problem Statement

A mind is the hard boundary of privacy, and one machine can hold several: two agents' minds and a person's. Memories and evidence are never edited. Where does a mind live, and what holds those rules if a bug tries to break them?

## Decision Drivers

* No owner can read another owner's mind.
* Memory content and evidence can't be edited, even by a bug.
* A mind can be backed up, moved or deleted as one thing.
* Start minimal ([ADR-0011](0011-start-minimal-and-measure.md)).

## Considered Options

* One SQLite file per mind, with the store refusing edits to content and evidence
* A database server

## Decision Outcome

Chosen option: "One SQLite file per mind, with the store refusing edits to content and evidence", because it is the minimal design, and a database server was more than minds on one machine needed.

* **A [mind](../primitives/MIND.md) is a file.** Privacy between minds is the boundary between files.
* **The store refuses edits** to memory content and evidence, so the never-edited rule holds even if code tries to break it.
* How much a memory is believed may be cached on the memory, but the cache can always be rebuilt from evidence ([ADR-0005](0005-belief-is-read-from-evidence.md)).
* A mind on an older layout refuses to open rather than guess.

### Consequences

* Good, because a mind is easy to back up, move and delete, and one mind can't reach another's rows.
* Good, because the most important invariant is held below the code.
* Bad, because one writer at a time per file means every process that writes has to share one connection ([ADR-0017](0017-one-store-per-mind-per-process.md)).
* Neutral, because a mind that leaves this machine, or is shared, may need a different store. If so, a record supersedes this one.

## Pros and Cons of the Options

### A database server

* Good, because many processes could write at once.
* Bad, because it adds a service to run and secure, while every mind lives on one machine.

## More Information

Written on 2026-10-14, after the fact.
