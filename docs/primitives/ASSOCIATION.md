# Association

A directed connection from one memory to another, with a strength between 0 and 1. The strength is how strongly recalling the first should bring the second to mind. Associations are how one memory leads to another: a question reaches a few memories directly, and associations carry recall outward from there, to memories that share no words with the question.

An association is born when a fact is written, and use decides what becomes of it. Facts written from the same moment are linked to each other, and a new fact is linked to the memories it builds on or corrects. After that, two memories prove useful together when both were in mind at a moment that taught something new.

Associations decide what comes to mind, never what can be seen. What a lookup can see is set by [namespaces](NAMESPACE.md) alone.

Deciding which memories a new fact builds on takes judgment, so whoever writes the fact makes that call: the agent noting it, or a model drawing it from an [episode](EPISODE.md). That facts written from one moment are linked to each other is fixed.

## Invariants

- **Associations carry no labels.** There's only one kind. Meaning lives in the memories, and in an association's direction and strength.
- Direction matters. The strength from A to B is independent of the strength from B to A.
- **Every association is born with a fact.** A fact is linked, as it's written, to the others written from the same moment and to the memories it builds on or corrects. No association is made between memories that already exist.
- **Strength governs recall, never belief.** A strong association never makes either memory more believed.
- An association strengthens only when both its memories were in mind at a moment that taught something new. Being recalled together isn't enough on its own.
- Associations weaken without use. Nothing is severed outright: a weak association becomes hard to follow but isn't gone.
- The total strength flowing into any one memory is bounded, so no memory can come to dominate recall just by being popular.
- **An association never makes a memory visible.** Associations exist only within a single mind and can cross namespaces, but recall follows one only when both ends are in a namespace being searched.
