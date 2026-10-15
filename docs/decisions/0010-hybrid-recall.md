# Recall combines meaning and keywords, then spreads along associations

## Context and Problem Statement

Recall first has to find the memories that bear on the moment directly. Search by meaning finds paraphrases but can miss exact names and rare words. Keyword search finds those but misses paraphrases. On which kind of search should recall start, and how are their results combined without making labels dishonest?

## Decision Drivers

* Recall finds both paraphrases and exact specifics.
* How well a memory matches is labelled honestly, whichever search found it.
* The choice is measured, not argued ([ADR-0011](0011-start-minimal-and-measure.md)).

## Considered Options

* Search by meaning only
* Keyword search only
* Both, merged by rank

## Decision Outcome

Chosen option: "Both, merged by rank", followed by spreading along [associations](../primitives/ASSOCIATION.md) ([ADR-0006](0006-unlabeled-directed-associations.md)).

* Both searches see exactly the same memories: the current ones in the namespaces being searched. Nothing else can affect the ranking.
* The rankings are merged by rank, not by score, so neither search's scale dominates.
* Keyword search can bring in a memory that meaning alone would have withheld, but it never changes how well the memory is labelled as matching. The label comes from meaning.
* How much weight each search gets is a setting, tuned on the harness.

On the harness, keyword ranking hurt on short facts but helped a great deal on long conversations (multi-session hit@1 rose from 0.70 to 0.90, temporal questions from 0.30 to 0.60), so both are kept, with keywords weighted lower.

### Consequences

* Good, because recall finds both paraphrases and exact names.
* Good, because the match label means the same thing whichever search found a memory.
* Bad, because the weighting was tuned on small samples and needs revisiting with more data.

## Pros and Cons of the Options

### Search by meaning only

* Good, because it is best on short facts.
* Bad, because it does worst on long conversations, where the answer turns on a specific word.

### Keyword search only

* Good, because it needs no model. It was kept as a baseline in the harness.
* Bad, because it misses paraphrases.

## More Information

Written on 2026-10-14, after the fact.
