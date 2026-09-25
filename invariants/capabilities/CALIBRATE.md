# Calibrate

Checks the mind's confidence against how things actually turned out, and adjusts two things:
- how much each kind of source is trusted;
- how quickly each kind of memory goes stale.

This is how every answer makes the mind better at knowing what it knows. A mind that says "likely" should be right about as often as "likely" implies.

## Invariants

- Calibration learns only from outcomes the agent didn't produce itself, such as the user's answers and the results of checks. It never learns from the agent's own guesses or its use of its own memories.
- The questions [Verify](VERIFY.md) picks at random count more than the ones it chose. Those chosen questions lean toward doubtful memories, so they would give a skewed picture on their own.
- Adjustments start from sensible defaults and move slowly. A handful of surprises doesn't overturn them.
- How well the mind's confidence matches reality can always be measured and reported.
- Changing how sources are trusted or how fast memories go stale changes future confidence. It never rewrites the evidence already recorded.
