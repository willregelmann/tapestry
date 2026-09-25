# Reasoner

The model that does Tapestry's background judgment, outside any conversation. It's a separate model reached over an API, not the agent holding the conversation. It answers questions like these:

- Does this statement contradict or replace a memory?
- Has a "no longer valid when" condition come true?
- What did this episode teach?
- Which scope does a new memory belong to?
- Is a new memory supported by the observations it came from?

## Invariants

- **The reasoner proposes, and never writes directly.** Everything it concludes goes through the same checks as any other evidence.
- It sees one mind at a time, and never more of it than the question needs.
- It reports its confidence in coarse categories, never as a precise number. Models state numbers with more certainty than they have.
- A new memory it proposes is rejected if it claims more than its sources say.
- If the reasoner is unavailable, its work waits. Nothing is guessed in its place, and memories waiting on its judgment are presented as possibly out of date.
- What was asked, which model answered and what it said are all recorded, so any judgment can be revisited when the model changes.
