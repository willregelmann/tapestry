# Mind

Everything one owner remembers: its memories, the associations between them, and the evidence behind them. The owner might be an agent like Ash, or a person working with an agent, as in Claude Code. A mind is the hard boundary of privacy and the unit Tapestry serves. One store can hold many minds for unrelated owners. Inside a mind, [scopes](SCOPE.md) divide what a session can see without walling it off.

## Invariants

- A mind belongs to exactly one owner, and every memory, association and piece of evidence belongs to exactly one mind.
- **Minds are private.** No agent reads or writes another owner's mind.
- Sharing is deliberate and rare. When one agent shares something, the receiving mind records it as evidence *from that agent*. It never takes on the other mind's belief or its confidence.
- A mind that can't be consulted says so. An agent is never shown "nothing relevant" when its mind was never actually searched.
