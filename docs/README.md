# The Map

These files are the map of the project. Apart from the intent and the decision records, each is a short description followed by statements that hold in every build of it.

- [INTENT.md](INTENT.md): what it's for, and what would make it pointless. Read it first.
- `primitives/`: what it's made of, and how each thing behaves.
- `capabilities/`: what it does that no one thing does.
- `interfaces/`: where the outside touches it, and what crosses.
- `decisions/`: why it has this shape, as decision records. The map never links to them.

## Reading the Map

- **The primitives are the core of the code.** Name the core after them, keep their boundaries, and hold each primitive's rules in that primitive. Everything around the core is the builder's choice.
- Where the map says nothing, the choice is the builder's.
- Bold statements are the ones the project would be a different project without.
- An invariant that says it catches up may be held sooner, never later.
- A build that can't keep an invariant changes the map, with a decision record, or finds another way. It never becomes a quiet exception.
- When the project changes, these files change in place.
- Before reversing something here, read the records in `decisions/` that explain it, and write a new record that supersedes them.
