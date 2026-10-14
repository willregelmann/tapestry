# Record decisions as MADR records beside the map

## Context and Problem Statement

The map in `docs/` says what Tapestry is and what must always hold. By design it carries no history and no rationale, so it can change in place. But the reasons behind its shape are worth keeping too: why memories are never edited, why namespaces replaced scopes, why every turn is filed in its own session. Until now those reasons were scattered across the build plan, commit messages and a research script. Where should the reasoning live, so that an agent about to change something can find why it is the way it is?

## Decision Drivers

* Someone who wants to reverse a decision can find why it was made and what was turned down.
* The map stays free of history, and the reasoning doesn't copy it.
* Records live next to the code and are reviewed in the same pull requests.

## Considered Options

* MADR records in `docs/decisions/`
* Reasoning kept in the build plan
* Reasoning kept in commit messages

## Decision Outcome

Chosen option: "MADR records in `docs/decisions/`", because it has a fixed place for the options and their trade-offs, and it sits beside the map it explains.

Conventions:

* **The map says what holds. A record says why.** Neither copies the other. A record links to the map files that hold its decision. The map never links to a record, because a record can be superseded and the map carries no history.
* **A record covers a pattern or a boundary, not a setting.** If changing a value, a threshold or a name in the code leaves the record true, that detail belongs in the build plan or the code.
* Records are numbered `NNNN-title-with-dashes.md` and start from `adr-template.md`.
* Records have no front matter. Version control holds the author and the date, and merging a record accepts it. A decision that hasn't been made stays out, as an open question in the plan.
* A decision is never changed by editing its record. A new record names the one it supersedes, and the old record gets `Superseded by ADR-NNNN.` under its title, which is the only edit an accepted record ever gets.
* A record stands on its own. It doesn't depend on tickets, chat threads or file paths that may move.
* A record written after the fact lists only options that were actually weighed, and marks unknown reasoning as `TODO` instead of guessing.

### Consequences

* Good, because the reasoning outlives changes to the plan and the map.
* Good, because the map can stay short and current.
* Bad, because records written after the fact are only as complete as what was written down at the time, and some carry `TODO`s.

## More Information

Written on 2026-10-14. Records 0001 to 0018 were written the same day, after the fact. MADR: <https://adr.github.io/madr/>.
