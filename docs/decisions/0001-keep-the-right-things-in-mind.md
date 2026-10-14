# Tapestry exists to answer "what should I have in mind right now?"

## Context and Problem Statement

Every other choice in Tapestry serves one question, and the question decides which choices matter. The first version of the map asked **"how much should I trust this right now?"**, and its tagline was "memory that knows it can be wrong". But the parts of the product that turned out to be most distinctive were how memory is segmented, how one memory brings others to mind, and how the mind turns over as the world changes. Trust is one part of that, not the whole. Which question should the product exist to answer?

## Decision Drivers

* The question names what the product does that ordinary agent memory doesn't.
* The question, the pitch and the failure sentence agree, so that every promise in the pitch can be traced to an invariant.
* The question covers what makes Tapestry distinctive: namespaces, associations and turnover.

## Considered Options

* "How much should I trust this right now?"
* "What should I have in mind right now?"

## Decision Outcome

Chosen option: "What should I have in mind right now?", because it is the one question that namespaces, associations and turnover each answer part of, with trust inside it rather than beside it.

* **[Namespaces](../primitives/NAMESPACE.md) decide what's in view.** Most of what an agent knows doesn't belong in this conversation.
* **[Associations](../primitives/ASSOCIATION.md) decide what comes to mind.** Some of what belongs shares no words with the moment.
* **Turnover decides what's still current.** [Memories](../primitives/MEMORY.md) are superseded, fade at the rate their kind of thing changes, and are consolidated between conversations. How much each is believed is part of being current.
* The product fails if an agent has the wrong things in mind: something stale or never actually said, something that belongs to another context, or less than it knew when it mattered.

### Consequences

* Good, because the pitch leads with what is actually distinctive.
* Good, because trust, the old question, survives as part of what's current, so nothing built for it is orphaned.
* Bad, because "what should I have in mind" is broader, and a feature can claim to serve it more easily than it could claim to serve trust. The failure sentence's three cases are the check.

### Confirmation

[INTENT.md](../INTENT.md) states the question and the failure sentence. Each of the three cases in the failure sentence is held by invariants in Namespace, Association and Memory.

## Pros and Cons of the Options

### "How much should I trust this right now?"

* Good, because it is sharp and easy to test: confidence is either earned or it isn't.
* Bad, because it leaves namespaces and associations without a reason to exist in the pitch.
* Bad, because "knows it can be wrong" describes an attitude, not what sets the product apart.

## More Information

Written on 2026-10-14, after the fact. The underlying stance, that fallibility is a side effect of useful memory, is recorded in [ADR-0002](0002-model-memory-on-human-memory-with-a-notebook.md).
