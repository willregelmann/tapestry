# Tapestry

Long-term memory for AI agents: memory that knows it can be wrong.

Whenever an agent is about to rely on something it remembers, Tapestry answers one question: **how much should I trust this right now?**

Finding a match is only half the answer. A memory can be on topic without answering the question. It can have been true once and be stale now. It can be something the agent guessed rather than something it was told. Names don't change, favorite colors do, and plans expire. Tapestry remembers how each belief was learned and how quickly that kind of thing tends to change. Its confidence fades when nothing renews it. When the stakes justify it, the agent simply asks: *"is your favorite color still pink?"*

It's built on one idea: fallibility isn't a flaw, it's a side effect. A memory that can't be wrong can't generalize, abstract or guess, and those are the abilities worth having. So Tapestry works the way human memory does. It connects memories by association, lets unused memories fade, keeps the gist, and strengthens the connections it uses. Where it departs from human memory, it does what people do: it keeps a notebook. Any belief can be traced back to what was said and who said it. Each agent's memory is its own, private by default and shared only on purpose. Every answer the agent gets back makes it better at knowing what it knows.

If an agent ever states a remembered fact with more confidence than it has earned, because the fact is stale, misattributed or was never actually said, Tapestry has failed at the one thing it exists to do.
