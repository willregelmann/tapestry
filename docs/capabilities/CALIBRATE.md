# Calibrate

Checks the mind's confidence against how things actually turned out, and adjusts two things:
- how much each kind of source is trusted;
- how quickly each kind of memory goes stale.

A mind that says "likely" should be right about as often as "likely" implies.

**Deferred.** Only these guardrails are settled for now.

## Invariants

- Calibration learns only from outcomes the agent didn't produce itself, such as the user's answers and the results of checks. It never learns from the agent's own guesses or its use of its own memories.
- Adjustments start from sensible defaults and move slowly. A handful of surprises doesn't overturn them.
- Changing how sources are trusted or how fast memories go stale changes future confidence. It never rewrites the evidence already recorded.
