"""The agent's tools, identical in every host (docs/interfaces/TOOLS.md).

Hosts expose these however they expose tools (Hermes provider tools, an MCP
server for Claude Code and the Agent SDK) and pass calls through here. Every
result is JSON. Failures say what did and didn't happen, so an error can never
be read as "memory holds nothing".
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from tapestry import belief, render
from tapestry.episode import Episode
from tapestry.mind import Mind, RecallFailed

_TOPICS_ARG = {"type": "array", "items": {"type": "string"},
               "description": "Topics to search alongside the general topic and the "
                              "session's open ones, e.g. [\"finance\"]. See tapestry_topics."}

# Recall output has to fit the agent's budget (docs/interfaces/TOOLS.md).
MAX_LIMIT = 20

SCHEMAS = [
    {"name": "tapestry_recall",
     "description": "Search your long-term memory on purpose, beyond what was recalled "
                    "automatically. Searches the general topic, the topics open in "
                    "this session, and any you name. Results carry the same labels.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to recall, in plain language."},
         "topics": _TOPICS_ARG,
         "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": MAX_LIMIT,
                   "description": f"Maximum memories, 1 to {MAX_LIMIT}."}},
         "required": ["query"]}},
    {"name": "tapestry_why",
     "description": "Show the evidence behind a memory: who said it, when, which topics "
                    "it was filed under, and what has superseded it since. A memory you "
                    "recalled by naming a topic needs that topic named here too.",
     "parameters": {"type": "object", "properties": {
         "memory": {"type": "integer", "description": "The #number of a recalled memory."},
         "topics": _TOPICS_ARG},
         "required": ["memory"]}},
    {"name": "tapestry_note",
     "description": "Remember a fact deliberately: something true, decided or preferred that is "
                    "worth knowing in a later conversation. Write it as a standalone statement. "
                    "File it under the topics where it matters.",
     "parameters": {"type": "object", "properties": {
         "content": {"type": "string", "description": "The memory, as a standalone statement."},
         "topics": {**_TOPICS_ARG, "description":
                    "Where to file it, e.g. [\"will\", \"finance\"]. Omit to file it where this "
                    "session happens (its project); pass [] for the general topic, which "
                    "every session searches."},
         "source": {"type": "string", "enum": ["user", "agent", "tool", "web"],
                    "description": "Who it came from: 'user' only for what the user said, "
                                   "'agent' for what you concluded or restated, 'tool' or "
                                   "'web' for what those reported."},
         "volatility": {"type": "string", "enum": list(belief.HALF_LIFE), "description":
                        "How quickly this kind of thing changes, which sets how fast belief in "
                        "it fades without new evidence: immutable (a name), stable, preference, "
                        "project (a plan), transient (what you're in the middle of). Omit if unsure."},
         "supersedes": {"type": "integer", "description":
                        "The #number of a recalled memory this note corrects or replaces. "
                        "That memory stops being recalled; tapestry_why still shows it. "
                        "Saying 'this replaces...' in the text doesn't do this."},
         "builds_on": {"type": "array", "items": {"type": "integer"}, "description":
                       "The #numbers of recalled memories this note adds to or depends on, "
                       "so recalling one brings the other to mind."}},
         "required": ["content", "source"]}},
    {"name": "tapestry_record_answer",
     "description": "Record the user's reply when you asked them whether a memory is still true: "
                    "confirmed, or contradicted. Only for a real answer from the user: not for "
                    "silence, and never for your own use or repetition of a memory. If they "
                    "contradict it and say what is true now, also save that with tapestry_note, "
                    "passing this memory as supersedes.",
     "parameters": {"type": "object", "properties": {
         "memory": {"type": "integer", "description": "The #number of the memory you asked about."},
         "answer": {"type": "string", "enum": ["confirmed", "contradicted"]},
         "topics": _TOPICS_ARG},
         "required": ["memory", "answer"]}},
    {"name": "tapestry_forget",
     "description": "Forget a memory completely, when the user asks you to: its evidence, its "
                    "associations and anything inferred only from it go too, and it can't be "
                    "undone. Call it first without confirmed to see what would go, tell the user, "
                    "and call it again with confirmed=true only after they agree.",
     "parameters": {"type": "object", "properties": {
         "memory": {"type": "integer", "description": "The #number of a recalled memory."},
         "confirmed": {"type": "boolean", "default": False,
                       "description": "True only once the user has agreed to forget it."},
         "topics": _TOPICS_ARG},
         "required": ["memory"]}},
    {"name": "tapestry_topics",
     "description": "List every topic with its description (never its contents), open "
                    "or close one for the rest of this session, or create one and describe it.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "open", "close", "describe"],
                    "default": "list"},
         "topic": {"type": "string", "description": "Topic name, for open, close or describe."},
         "description": {"type": "string", "description": "For describe: what the topic "
                         "holds, in one line, so it can be found without opening it."}}}},
]


class Session:
    """What a tool call needs: the mind, the session's open topics, the place it happens
    in, its episode when the host keeps one, and how to show text to this host."""

    def __init__(self, mind: Mind, open_topics: list[str], *,
                 place: str | None = None, episode: Episode | None = None,
                 on_change: Callable[[list[str]], None] | None = None,
                 escape: Callable[[str], str] = lambda s: s) -> None:
        self.mind = mind
        self.open = open_topics
        self.place = place
        self.episode = episode
        self._changed = on_change
        self._escape = escape

    def searched(self, extra: list[str] = ()) -> list[str]:
        """What this session sees besides the general topic: its open topics, and any
        named for one lookup."""
        return list(dict.fromkeys([*self.open, *extra]))

    def call(self, name: str, args: dict[str, Any] | None) -> str:
        args = args or {}
        try:
            return json.dumps(self._call(name, args))
        except RecallFailed as e:
            return json.dumps({"error": f"recall failed ({e}); no memories were searched"})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}; nothing was changed"})

    def _unknown(self, names: list[str]) -> list[str]:
        known = {t["name"] for t in self.mind.topics()}
        return [n for n in names if n not in known]

    @staticmethod
    def _memory_id(ref: Any) -> int | None:
        """A memory's #number from an int or "#N"/"N" (the descriptions say "#number").
        None for anything else, bools included (True would otherwise be #1)."""
        if isinstance(ref, str):
            ref = ref.strip().removeprefix("#")
            return int(ref) if ref.isascii() and ref.isdigit() else None
        return ref if isinstance(ref, int) and not isinstance(ref, bool) else None

    def _call(self, name: str, args: dict[str, Any]) -> dict:
        if name == "tapestry_recall":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return {"error": "no query received, so no search ran"}
            extra = list(args.get("topics") or [])
            missing = self._unknown(extra)
            if missing:
                return {"error": f"no topic {', '.join(missing)}; nothing was searched. "
                                 "tapestry_topics lists the ones that exist."}
            searched = self.searched(extra)
            try:
                limit = int(args.get("limit") or 5)
            except (TypeError, ValueError):
                return {"error": f"limit must be a whole number from 1 to {MAX_LIMIT}; "
                                 "nothing was searched"}
            limit = max(1, min(limit, MAX_LIMIT))
            hits = self.mind.recall(query, topics=searched, k=limit)
            result = {"searched": ["general", *searched]}
            if not hits:
                return {**result, "memories": "", "note": "searched; nothing related found"}
            return {**result, "memories": self._escape(render.budgeted(hits, render.PROMPT_BUDGET, via_tool=False))}
        if name == "tapestry_why":
            mid = self._memory_id(args.get("memory"))
            if mid is None:
                return {"error": f"memory must be a #number, got {args.get('memory')!r}"}
            # Like recall, why can name topics for this one lookup: naming one IS searching
            # it, so a memory recall just returned from there can be explained ("Why do you
            # believe this?" always has an answer, docs/primitives/EVIDENCE.md).
            extra = list(args.get("topics") or [])
            missing = self._unknown(extra)
            if missing:
                return {"error": f"no topic {', '.join(missing)}; nothing was looked up. "
                                 "tapestry_topics lists the ones that exist."}
            # A memory outside what's searched gets the same answer as one that doesn't
            # exist: a topic that isn't searched leaves no trace (docs/primitives/TOPIC.md).
            ev = self.mind.evidence(mid) if self.mind.visible(mid, self.searched(extra)) else []
            if not ev and self.mind.was_forgotten(mid):
                return {"error": f"memory #{mid} was forgotten"}
            if not ev:
                return {"error": f"no memory #{mid} in the topics searched; if it's filed "
                                 "in another, name that topic or open it"}
            b, now = self.mind.belief(mid), time.time()
            return {"memory": mid, "belief": {"label": f"{b.word} ({min(b.p, 0.99):.2f})", "because": belief.why(b, now)},
                    "evidence": [{**e, "when": render.learned(e["ts"])} for e in ev]}
        if name == "tapestry_record_answer":
            mid = self._memory_id(args.get("memory"))
            if mid is None:
                return {"error": f"memory must be a #number, got {args.get('memory')!r}; nothing recorded"}
            if args.get("answer") not in ("confirmed", "contradicted"):
                return {"error": "answer is 'confirmed' or 'contradicted'; nothing recorded"}
            extra = list(args.get("topics") or [])
            missing = self._unknown(extra)
            if missing:
                return {"error": f"no topic {', '.join(missing)}; nothing was recorded. "
                                 "tapestry_topics lists the ones that exist."}
            if not self.mind.visible(mid, self.searched(extra)):
                return {"error": f"no memory #{mid} in the topics searched; if it's filed "
                                 "in another, name that topic or open it. Nothing recorded."}
            by = self.mind.superseded_by(mid)
            if by is not None:
                return {"error": f"#{mid} was superseded by #{by}; answer about #{by} instead. Nothing recorded."}
            (self.mind.confirm if args["answer"] == "confirmed" else self.mind.contradict)(
                mid, source="user_confirmed")
            b = self.mind.belief(mid)
            return {"recorded": args["answer"], "memory": mid,
                    "belief": {"label": f"{b.word} ({min(b.p, 0.99):.2f})", "because": belief.why(b, time.time())}}
        if name == "tapestry_forget":
            mid = self._memory_id(args.get("memory"))
            if mid is None:
                return {"error": f"memory must be a #number, got {args.get('memory')!r}; nothing forgotten"}
            extra = list(args.get("topics") or [])
            missing = self._unknown(extra)
            if missing:
                return {"error": f"no topic {', '.join(missing)}; nothing was forgotten. "
                                 "tapestry_topics lists the ones that exist."}
            if not self.mind.visible(mid, self.searched(extra)):
                return {"error": f"no memory #{mid} in the topics searched; if it's filed "
                                 "in another, name that topic or open it. Nothing forgotten."}
            plan = self.mind.forgetting(mid)
            if args.get("confirmed") is not True:
                return {"forgotten": False, "memory": mid,
                        "says": self._escape(self.mind.content(mid)),
                        "would_also_forget": len(plan.ids) - 1,
                        "would_become_current_again": plan.restored,
                        "note": "Nothing has been forgotten yet. Tell the user what will go and ask; "
                                "call again with confirmed=true only after they agree."}
            done = self.mind.forget(mid)
            return {"forgotten": True, "memories": len(done.ids),
                    "became_current_again": done.restored}
        if name == "tapestry_note":
            content = args.get("content")
            if not isinstance(content, str) or not content.strip():
                return {"error": "empty note; nothing saved"}
            source = args.get("source")
            if source not in ("user", "agent", "tool", "web"):
                return {"error": "every note says who it came from: source is 'user' (the "
                                 "user said it), 'agent' (you concluded or restated it), "
                                 "'tool' or 'web'. Nothing saved."}
            names = ([self.place] if self.place else []) if args.get("topics") is None \
                else list(args["topics"])
            old = args.get("supersedes")
            if old is not None:
                ref, old = old, self._memory_id(old)
                if old is None:
                    return {"error": f"supersedes must be a memory's #number, got {ref!r}; "
                                     "nothing saved"}
                # Visibility first, and an unknown id gets the same answer as a hidden one:
                # otherwise the refusal tells a session which ids exist in topics it can't
                # see (and whether they've been superseded).
                if not self.mind.visible(old, self.searched(names)):
                    return {"error": f"no memory #{old} this session can see; if it's filed in "
                                     "another topic, open that or file this note there. "
                                     "Nothing saved."}
                by = self.mind.superseded_by(old)
                if by is not None:
                    return {"error": f"#{old} was already superseded by #{by}; supersede #{by} "
                                     "instead if it's the one that's wrong. Nothing saved."}
            builds_on = [self._memory_id(b) for b in args.get("builds_on") or []]
            if any(b is None or not self.mind.visible(b, self.searched(names)) for b in builds_on):
                return {"error": f"builds_on must be #numbers of memories this session can see, got "
                                 f"{args.get('builds_on')!r}; nothing saved"}
            # Born linked to what it builds on and corrects, and to whatever else was noted in
            # this moment of the conversation (docs/primitives/ASSOCIATION.md).
            moment = self.episode.noted_this_moment() if self.episode else []
            # Whoever said the new thing, deciding that it replaces the old one was the
            # agent's call, so the supersede evidence is the agent's.
            mid = self.mind.remember(content.strip(), source=source, topics=names,
                                     supersedes=old, superseded_by_judgment_of="agent",
                                     volatility=args.get("volatility"),
                                     episode=self.episode.id if self.episode else None,
                                     associate=[*moment, *builds_on, *([old] if old is not None else [])])
            out = {"saved": mid, "topics": names or ["general"]}
            return {**out, "supersedes": old} if old is not None else out
        if name == "tapestry_topics":
            action, topic = args.get("action") or "list", (args.get("topic") or "").strip()
            if action != "list" and not topic:
                return {"error": f"{action} needs a topic name"}
            if action == "describe":
                if not (args.get("description") or "").strip():
                    return {"error": "describe needs a description"}
                self.mind.topic(topic, args["description"])
            elif action == "open":
                if self._unknown([topic]):
                    return {"error": f"no topic {topic!r}; describe it first to create it"}
                if topic not in self.open:
                    self.open.append(topic)
            elif action == "close":
                self.open[:] = [n for n in self.open if n != topic]
            if action in ("open", "close") and self._changed:
                self._changed(self.open)
            return {"topics": self.mind.topics(), "open": self.open,
                    "note": "The general topic is always searched."}
        return {"error": f"unknown tool {name}"}
