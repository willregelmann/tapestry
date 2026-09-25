# Scope

A region of a [mind](MIND.md) that can be loaded or set aside: everything the owner knows about one project, one domain or one part of their life. A user-wide scope holds what's true everywhere. A project's scope holds what's only true there. Loading a scope makes what the mind knows about that area available, much as loading a skill makes a way of working available.

A mind is one web of memories, and scopes don't cut it apart. They decide what a session can see. The same memory can be reached from any direction, as long as the session can see where the path leads.

## Invariants

- Every [memory](MEMORY.md) lives in exactly one scope.
- A session starts with the scopes that fit where it is: usually the user-wide scope plus the scope of the current project. Other scopes are loaded deliberately, and every load is visible.
- **Associations can cross scopes.** Recall follows one only when both ends are in loaded scopes.
- **An unloaded scope leaves no trace.** Its memories, and the associations that lead into it, don't show up in results, don't affect ranking and don't count against any limit.
- An observation lands in the scope of the session where it happened. A memory learned from it can live in a wider scope when it's about the owner rather than the project. "Will prefers terse answers" belongs to the user-wide scope; "this repo uses pytest" belongs to the project.
- Moving a memory to another scope is recorded as [evidence](EVIDENCE.md), never done silently.
- Scopes are soft partitions within one mind. They are never a way into another mind.
