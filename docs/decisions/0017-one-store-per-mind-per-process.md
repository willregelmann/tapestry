# One store per mind per process, shared by every provider

## Context and Problem Statement

Hermes builds a memory provider for every agent, and builds agents for every session, every scheduled run and again on resume. Agents can be dropped without the provider ever being shut down. Each provider owned an embedding model, a database connection and a writer thread, and the blocked thread kept the provider alive, so nothing was freed. On a Raspberry Pi the host grew from about 400MB to 1.4GB plus full swap in 14 hours, and hung until the hardware watchdog reset it. How should resources be owned?

## Decision Drivers

* Creating or dropping a provider costs almost nothing.
* A provider that is never shut down leaks nothing.
* Only one connection ever writes to a mind.

## Considered Options

* Each provider owns its own model, connection and writer
* One model per process, and one store per mind per process, shared by every provider

## Decision Outcome

Chosen option: "One store per mind per process".

* The embedding model is loaded once per process.
* Each mind has one store per process: a read connection, a write queue and a single writer thread, shared by every provider for that mind.
* A provider is a few small objects. Shutting one down flushes its queued writes and detaches. The store lives as long as the process.

Measured with 20 providers, half shut down and half dropped: the old design grew from 110 to 436MB, 13 to 51 file handles and 2 to 11 threads. The shared store stayed at 112MB, 13 file handles and 2 threads, and both wrote all 40 rows.

### Consequences

* Good, because a host that never shuts providers down no longer leaks.
* Good, because one writer per mind fits a file that allows one writer at a time ([ADR-0009](0009-one-file-per-mind.md)).
* Bad, because one process's providers share a write queue, so a slow write delays the others.

## More Information

Written on 2026-10-14, after the fact.
