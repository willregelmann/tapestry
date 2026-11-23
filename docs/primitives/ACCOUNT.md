# Account

A person who owns [minds](MIND.md). The person is outside Tapestry; the account is what Tapestry keeps about them: who they are, the minds they own, and the [keys](KEY.md) they've given agents to those minds. Will's account owns his own mind, which Claude Code attaches to, and the minds Ash and Wren attach to.

Every mind is its person's. Agents attach only to the minds they've been given, and the person has every right over all of them.

## Invariants

- **An account owns minds and never remembers anything itself.** Nothing about recall, belief or evidence depends on which account owns a mind.
- A person signs in with an identity they already have somewhere else. Tapestry never holds a password for them.
- Every mind is owned by exactly one account from the moment it exists. Only the account's person creates a mind. No agent, key or machine can.
- **The account's person has every right over every mind it owns.** Any agent holding a key to one does what they ask of it, as far as the key allows, and no agent can refuse them. They can look at and change any of them through the [viewer](../interfaces/VIEWER.md), and destroy one through [commands](../interfaces/COMMANDS.md).
- **Accounts are walled off from each other.** Nothing done in one account can reach another account's minds, list them, or learn that they exist.
- The account's person decides which agents hold keys to each of its minds, and can revoke any key at any time.
- Destroying a mind, or closing the account, is complete, the way forgetting a memory is.
- Every change to what an account owns, such as a mind created or destroyed, or a key approved or revoked, is recorded, and the person can always see that record.
