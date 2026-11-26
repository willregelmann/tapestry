# A source is fetched when the agent asks, and drawn by the background job once its session ends

## Context and Problem Statement

The agent can have a source read (a page, a paper, a document). Reading it means two things that happen at different times and in different places: fetching its words from wherever it is found, and drawing what it says into memory with a model. Where does each happen, and how does a fact remember where it was read once the reading has gone?

## Decision Drivers

* Background jobs reach nothing outside but the model the owner chose.
* The agent hears at once whether a source can be read, and never finds out later that it silently wasn't.
* What was read is drawn by the same job, and through the same chosen model, as a conversation.
* A fact read from a source can say where it came from after the reading's words have gone.

## Considered Options

* Fetch in the tool call, store the passages as the reading's turns, draw in the background job after the session ends
* Queue the address in the tool call, and fetch and draw it in the background job
* Fetch and draw in the tool call

## Decision Outcome

Chosen option: "Fetch in the tool call, store the passages as the reading's turns, draw in the background job after the session ends", because it keeps the background job's reach to the chosen model, tells the agent at once when a source can't be read, and lets a reading wait, be drawn, be drawn again and expire exactly as a conversation does.

Each entry of evidence written from a reading cites the source in the entry itself, as where it is found, its title and the day it was read, so it survives the reading's expiry.

### Consequences

* Good, because the drawing path, the expiry and the re-drawing of episodes serve readings unchanged.
* Good, because a source that can't be read is an error in the agent's own turn.
* Bad, because the tool call waits on the fetch, which can take seconds for a large PDF.
* Bad, because a reading waits until the session that asked for it ends, so nothing it says can be recalled in that session.

### Confirmation

* `tests/test_reading.py`: a source that can't be read says so when asked and nothing waits; a reading waits until the session that asked for it has ended; evidence still names the source once the reading has gone.
* `tests/test_hermes.py`: a Hermes agent can have a source read, and it waits for its session to end.
* [Jobs](../interfaces/JOBS.md), [Episode](../primitives/EPISODE.md), [Evidence](../primitives/EVIDENCE.md).

## Pros and Cons of the Options

### Queue the address in the tool call, and fetch and draw it in the background job

The bench prototype (`bench/study_mcp.py`) fetched to check, then fetched again after the session.

* Good, because the tool call is quick.
* Bad, because the background job would reach the web, which Jobs allows only for the chosen model.
* Bad, because the words drawn could differ from the words checked.

### Fetch and draw in the tool call

* Good, because what was read can be recalled in the same session.
* Bad, because a model call per window inside a tool call takes minutes for a paper, and the conversation's drawing is already a background job.
