# Belief is read from evidence, as "right when last checked" times "still true now"

## Context and Problem Statement

Tapestry has to say how much each memory is believed, in a way that fades when nothing renews it, that weighs sources differently, and that can always be explained. Several models of belief are in use or in the literature. Which one should Tapestry use, and where does belief live?

## Decision Drivers

* Belief is calibrated: "likely" is right about as often as "likely" implies.
* A fact that hasn't been checked in a while is believed less, at a rate set by how quickly that kind of thing changes.
* Belief can always be rebuilt and explained from the record.
* Wrong defaults for how fast things change can be corrected from what the user later says.

## Considered Options

* Additive scoring, as in Hindsight: add for a confirmation, subtract twice as much for a contradiction, no decay over time
* Beta-Bernoulli counts weighted by source, with the counts forgetting toward even odds
* A two-factor hazard model: whether it was right when last checked, times whether it is still true now, with a decay rate per kind of fact
* The hazard model, with the decay rates learned online from changes the user reports

## Decision Outcome

Chosen option: "A two-factor hazard model", with online learning of decay rates deferred until there is enough data.

* **Belief is never stored as the truth. It is read from [evidence](../primitives/EVIDENCE.md).** Replaying a memory's evidence gives its confidence at any moment. Any cached value is only a cache.
* **Belief has two factors**: right when last checked, which evidence moves by how reliable its source is, and still true now, which decays with time at a rate set by the kind of thing it is. A name barely fades, a preference slowly does, and a plan expires.
* **Sources are weighted.** A direct answer from the user counts most, the agent's own inference least, and the agent's own use of a memory not at all.

On synthetic histories over three years, the hazard model had the best Brier score (0.035, against 0.061 for additive and 0.074 for Beta-Bernoulli) and the best calibration error (0.006, against 0.034 and 0.121). With decay rates four times too long it still scored 0.037, with calibration error 0.015, and learning the rates online brought calibration error back to 0.006.

### Consequences

* Good, because belief is calibrated, explainable and rebuildable.
* Good, because recalibrating changes future readings without touching the record.
* Bad, because each memory needs a kind of thing, which a model has to classify.
* Bad, because the decay defaults are guesses until enough changes have been observed to learn them.

### Confirmation

[MEMORY.md](../primitives/MEMORY.md) holds the rule that belief is read from evidence, and the harness measures calibration on a labelled set.

## Pros and Cons of the Options

### Additive scoring

* Good, because it is simple to explain.
* Bad, because nothing decays, so a fact last confirmed years ago looks as solid as one confirmed today.

### Beta-Bernoulli with forgetting

* Good, because it is a standard model of evidence.
* Bad, because forgetting pulls every belief toward even odds, which made it the worst calibrated of the models tested.

### Hazard model with learned decay rates

* Good, because it corrects wrong defaults.
* Bad, because it needs many observed changes per kind of fact before it helps, so it is deferred.

## More Information

Written on 2026-10-14, after the fact. The comparison is in `research/confidence_sim.py`. Related research: Nous (2606.22030), Hindsight (2512.12818), MemTX (2607.23929).
