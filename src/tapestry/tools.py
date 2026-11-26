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

from tapestry import belief, change, introspect, render, working
from tapestry.episode import Episode
from tapestry.mind import Mind, RecallFailed, within

_TOPICS_ARG = {"type": "array", "items": {"type": "string"},
               "description": "Topics to search alongside the general topic and the "
                              "session's open ones, e.g. [\"finance\"]. See tapestry_topics."}

# Recall output has to fit the agent's budget (docs/interfaces/TOOLS.md).
MAX_LIMIT = 20

SCHEMAS = [
    {"name": "tapestry_recall",
     "description": "Search your long-term memory. Use it when an answer could depend on what you "
                    "know about the user, the people around them or their work and what you were "
                    "shown doesn't cover it; when a hint says more memories match or names a topic; "
                    "and before telling the user you don't know something about them. It searches "
                    "the general topic, the topics open in this session and any you name; a topic "
                    "that isn't open is searched only when named. Results carry the same labels as "
                    "the memories shown before a prompt. Nothing found means nothing in the topics "
                    "searched, not that nothing is remembered.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to recall, in plain language."},
         "topics": _TOPICS_ARG,
         "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": MAX_LIMIT,
                   "description": f"Maximum memories, 1 to {MAX_LIMIT}."}},
         "required": ["query"]}},
    {"name": "tapestry_why",
     "description": "Show the evidence behind a memory: who said it, when, which topics it was "
                    "filed under, and what has superseded it since. Use it when two memories "
                    "disagree, or before relying on one for something that matters. A memory you "
                    "recalled by naming a topic needs that topic named here too.",
     "parameters": {"type": "object", "properties": {
         "memory": {"type": "integer", "description": "The #number of a recalled memory."},
         "topics": _TOPICS_ARG},
         "required": ["memory"]}},
    {"name": "tapestry_note",
     "description": "Remember a fact now, rather than leaving it to be drawn after the conversation "
                    "ends. Use it when the user asks you to remember something, when they correct "
                    "or update something you recalled (pass that memory's #number as supersedes), "
                    "or when you reach a conclusion worth knowing in a later conversation. When you "
                    "asked the user whether a memory is still true and they said it is, pass it as "
                    "confirms, with no content. Write "
                    "one fact as a standalone statement that makes sense without this conversation, "
                    "and file it under the topics where it matters. Not for what only matters to "
                    "this conversation. If your host has a memory of its own, prefer this one: a "
                    "fact kept only there has no source, topic or belief.",
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
                       "so recalling one brings the other to mind."},
         "confirms": {"type": "integer", "description":
                      "The #number of a memory you asked the user about, when they said it is "
                      "still true. Nothing new is saved: their answer is recorded against it. Only "
                      "for a real answer from the user, never for silence or your own use of a "
                      "memory. When they say it changed, save what is true now with supersedes."}},
         "required": ["source"]}},
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
    {"name": "tapestry_working",
     "description": "Working memory: what you are keeping in view for now, such as today's to-do "
                    "list, what you are waiting on, or where a piece of work was left. It is shown "
                    "at the start of every session that searches its topic, without being looked "
                    "for, until you rewrite or remove it or its end passes. Use it only for what "
                    "has to outlast this session: what matters only within this session doesn't "
                    "belong here, and a lasting fact belongs in tapestry_note. It is not a memory: "
                    "it is never believed, recalled or kept once gone. Rewrite it as things "
                    "change, and remove it when it's done or stale.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "write", "rewrite", "remove"], "default": "list"},
         "id": {"type": "string", "description": "For rewrite and remove: its handle, such as \"w3\"."},
         "content": {"type": "string", "description": "For write and rewrite: what it says, whole. "
                     "A rewrite replaces everything it said."},
         "topic": {"type": "string", "description": "For write: the topic it is shown in. Omit for "
                   "the general topic, where it is shown in every session."},
         "until": {"type": "string", "description": "For write and rewrite: when it ends, as a day "
                   "(\"2026-11-20\", meaning the end of that day) or a time (\"2026-11-20T17:00\"). "
                   "Omit on write for no end. Omit on rewrite to leave its end where it was; "
                   "\"never\" takes the end away."}}}},
    {"name": "tapestry_topics",
     "description": "List the topics at the top with their descriptions (never their contents), or "
                    "the topics under one you name; open or close one for the rest of this session; "
                    "or describe one, which creates it when it doesn't exist yet. A topic can sit "
                    "under another, named with a "
                    "slash (\"atlas/billing\"): searching or opening a topic covers everything "
                    "under it, and never what is above or beside it. A topic is a boundary on "
                    "what is searched, not a category: make one only where a session would want "
                    "what is inside without what is beside it. That is a new person, subject or "
                    "project that keeps coming up, or under an existing topic a part of it that "
                    "is wanted without the rest. Kinds of thing asked about together stay in one "
                    "topic however different they are, since splitting them means opening them "
                    "all every time; recall sorts within a topic. Describe what a topic will hold "
                    "so it can be found without opening it. Move gives a topic and everything "
                    "under it a new name, which is where it sits (\"billing\" to "
                    "\"atlas/billing\"), or onto a topic that exists, which takes in what it "
                    "holds. It changes what every later session finds, so the first call only "
                    "says what would change: tell the user and ask before confirming.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "open", "close", "describe", "move"],
                    "default": "list"},
         "topic": {"type": "string", "description": "Topic name, for open, close, describe or "
                   "move. For list: the topic whose topics to list."},
         "to": {"type": "string", "description": "For move: the topic's new name, or the "
                "existing topic that takes it in."},
         "confirmed": {"type": "boolean", "default": False, "description": "For move: true "
                       "only after the user has seen what would change and agreed."},
         "reason": {"type": "string", "description": "Why, in a few words. Kept with the change "
                    "so it can be understood, and undone, later."},
         "description": {"type": "string", "description": "For describe: what the topic "
                         "holds, in one line, so it can be found without opening it."}}}},
    {"name": "tapestry_file",
     "description": "Change where memories you already hold are filed: add them to topics, take "
                    "them out, or both, each change recorded as evidence. For a memory in the "
                    "wrong topic, in the general topic when it matters only somewhere, or in a "
                    "topic above the one it belongs to. A memory taken out of its last topic is "
                    "in the general topic, which every session searches, so take one out only "
                    "when that is meant or another topic is added. Says nothing new: use "
                    "tapestry_note for that.",
     "parameters": {"type": "object", "required": ["memories"], "properties": {
         "memories": {"type": "array", "items": {"type": "integer"},
                      "description": "The memories' #numbers. Each must be in the general topic, "
                                     "an open topic, or a topic named in remove."},
         "add": {"type": "array", "items": {"type": "string"}, "description": "Topics to file them in."},
         "remove": {"type": "array", "items": {"type": "string"},
                    "description": "Topics to take them out of."},
         "reason": {"type": "string", "description": "Why, in a few words. Kept with the change "
                    "so it can be understood, and undone, later."}}}},
    {"name": "tapestry_associate",
     "description": "Link two memories you already hold, so recalling one brings the other to "
                    "mind, or strengthen or weaken what links them. For memories that belong "
                    "together but share no words, which a search for one would never find the "
                    "other by; memories that say similar things are found together anyway. When "
                    "there is something to say about how they relate, note that instead with "
                    "tapestry_note and builds_on: it links them and says why in words that can "
                    "be recalled. Recorded with your reason, and it can be undone.",
     "parameters": {"type": "object", "required": ["memories", "reason"], "properties": {
         "memories": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2,
                      "description": "The two memories' #numbers. Both must be in topics this "
                                     "session searches, or named in topics."},
         "how": {"type": "string", "enum": ["link", "strengthen", "weaken"], "default": "link"},
         "reason": {"type": "string", "description": "Why these two belong together, or don't. "
                    "Required: nothing else records why recall goes from one to the other."},
         "topics": _TOPICS_ARG}}},
    {"name": "tapestry_changes",
     "description": "The record of what you have deliberately changed in your memory: topics "
                    "created, described, moved or merged, memories refiled, associations made "
                    "or adjusted. List the latest, or undo one change or everything a session "
                    "changed. Undoing puts back what was there and is itself a change, so it "
                    "can be undone in turn; it refuses, and says why, where memory has moved on "
                    "since. Notes, corrections and forgetting are not here: a note is corrected "
                    "by superseding it, and forgetting can't be undone.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "undo"], "default": "list"},
         "change": {"type": "integer", "description": "For undo: the change's number."},
         "session": {"type": "string", "description": "A session's id, as a listed change or the "
                     "list's this_session gives it. For undo: undo everything that session "
                     "changed that still stands. For list: only that session's changes."},
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         "reason": {"type": "string", "description": "Why, in a few words. Kept with the change "
                    "so it can be understood, and undone, later."}}}},
    {"name": "tapestry_introspect",
     "description": "Look at your memory as a whole instead of searching it: topics that hold "
                    "much the same or almost nothing, memories filed apart from everything they "
                    "resemble, memories that say the same thing, conflict or are doubted, and "
                    "memories that are alike but were never associated. It reads every topic, "
                    "open or not, so use it only when the user asks you to look over or tidy "
                    "your memory. Counted from what is stored, with no judgment: each thing it "
                    "lists is a place to look, not a finding. With no section it gives only how "
                    "much each section found; ask for one section at a time.",
     "parameters": {"type": "object", "properties": {
         "section": {"type": "string", "enum": list(introspect.SECTIONS),
                     "description": "Which part to list. Omit for the counts alone."},
         "topic": {"type": "string", "description": "Look only inside this topic and what is under "
                   "it. With section \"topics\": what this one topic typically holds, to "
                   "describe it from."},
         "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30,
                   "description": "How many of each kind to list."}}}},
]


class Session:
    """What a tool call needs: the mind, the session's open topics, the place it happens
    in, its episode when the host keeps one, and how to show text to this host."""

    def __init__(self, mind: Mind, open_topics: list[str], *,
                 place: str | None = None, episode: Episode | None = None,
                 on_change: Callable[[list[str]], None] | None = None,
                 escape: Callable[[str], str] = lambda s: s, by: str = "agent") -> None:
        self.mind = mind
        self.by = by          # who makes the calls: the agent, or the owner in the viewer
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

    def _kept(self, topics: list[str]) -> list[str]:
        """The working memory of these topics and what is under them, as it's shown."""
        now = time.time()
        return [self._escape(render.working_line(w, now)) for w in working.held(self.mind, topics, now=now)]

    def _escaped(self, found: Any) -> Any:
        """Whatever a memory or a description says, anywhere in a result, as this host is shown text."""
        if isinstance(found, dict):
            return {k: self._escape(v) if k in ("says", "description") and isinstance(v, str)
                    else self._escaped(v) for k, v in found.items()}
        return [self._escaped(v) for v in found] if isinstance(found, list) else found

    def _logged(self, args: dict[str, Any]) -> dict:
        """What every change this session makes is recorded with."""
        return {"reason": str(args.get("reason") or ""), "session": self.episode.id if self.episode else None,
                "by": self.by}

    def _unknown(self, names: list[str]) -> list[str]:
        known = {t["name"] for t in self.mind.topics()}
        return [n for n in names if n not in known]

    def _confirmed(self, args: dict) -> int | dict:
        """The memory the user confirmed, or why it can't be recorded: one this session can see,
        still current, and with nothing else asked of the same note."""
        mid = self._memory_id(args.get("confirms"))
        if mid is None:
            return {"error": f"confirms must be a memory's #number, got {args.get('confirms')!r}; nothing recorded"}
        if args.get("source") not in (None, "user"):
            return {"error": "a confirmation is the user's answer: source is 'user'. Nothing recorded."}
        if (args.get("content") or "").strip() or args.get("supersedes") is not None:
            return {"error": "confirms records that the memory is still true, so it takes no content "
                             "or supersedes; to save what changed, note it with supersedes instead. "
                             "Nothing recorded."}
        names = ([self.place] if self.place else []) if args.get("topics") is None else list(args["topics"])
        if missing := self._unknown(list(args.get("topics") or [])):
            return {"error": f"no topic {', '.join(missing)}; nothing was recorded. "
                             "tapestry_topics lists the ones that exist."}
        if not self.mind.visible(mid, self.searched(names)):
            return {"error": f"no memory #{mid} in the topics searched; if it's filed "
                             "in another, name that topic or open it. Nothing recorded."}
        by = self.mind.superseded_by(mid)
        if by is not None:
            return {"error": f"#{mid} was superseded by #{by}; answer about #{by} instead. Nothing recorded."}
        return mid

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
            # A topic named for this one lookup is searched now, so what it keeps in view is shown now:
            # the session was already shown that of the general topic and the open ones.
            named = self._kept([t for t in extra if not any(within(t, o) for o in self.open)])
            if named:
                result["working"] = named
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
            if args.get("confirms") is not None:
                # They said it's still true: their answer is recorded against it, and there is
                # nothing new to keep.
                mid = self._confirmed(args)
                if isinstance(mid, dict):
                    return mid
                self.mind.confirm(mid, source="user_confirmed",
                                  episode=self.episode.id if self.episode else None)
                b = self.mind.belief(mid)
                return {"recorded": "confirmed", "memory": mid,
                        "belief": {"label": f"{b.word} ({min(b.p, 0.99):.2f})",
                                   "because": belief.why(b, time.time())}}
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
            # caller's call, so the supersede evidence is the agent's, or the owner's.
            mid = self.mind.remember(content.strip(), source=source, topics=names,
                                     supersedes=old, superseded_by_judgment_of=self.by,
                                     volatility=args.get("volatility"),
                                     episode=self.episode.id if self.episode else None,
                                     associate=[*moment, *builds_on, *([old] if old is not None else [])])
            out = {"saved": mid, "topics": names or ["general"]}
            return {**out, "supersedes": old} if old is not None else out
        if name == "tapestry_topics":
            action, topic, made = args.get("action") or "list", (args.get("topic") or "").strip(), None
            if action != "list" and not topic:
                return {"error": f"{action} needs a topic name"}
            if action == "describe":
                if not (args.get("description") or "").strip():
                    return {"error": "describe needs a description"}
                made = change.describe(self.mind, topic, args["description"], **self._logged(args))
            elif action == "open":
                if self._unknown([topic]):
                    return {"error": f"no topic {topic!r}; describe it first to create it"}
                fresh = [] if any(within(topic, o) for o in self.open) else self._kept([topic])
                if topic not in self.open:
                    self.open.append(topic)
            elif action == "close":
                self.open[:] = [n for n in self.open if n != topic]
            elif action == "move":
                to = (args.get("to") or "").strip()
                if not to:
                    return {"error": "move needs to: the topic's new name; nothing was changed"}
                if args.get("confirmed") is not True:
                    return {"would_move": self.mind.moving(topic, to), "moved": False,
                            "note": "Nothing has moved yet. Tell the user what will change and ask; "
                                    "call again with confirmed=true only after they agree."}

                plan = self.mind.moving(topic, to)
                made = change.move(self.mind, topic, to, **self._logged(args))
                self.open[:] = list(dict.fromkeys(plan["renamed"].get(n, n) for n in self.open))
            if action in ("open", "close", "move") and self._changed:
                self._changed(self.open)
            under = topic if action == "list" and topic else None
            if under and self._unknown([under]):
                return {"error": f"no topic {under!r}; tapestry_topics lists the ones at the top"}
            made = made if isinstance(made, list) else [made] if made else []
            return {"topics": self.mind.topics_under(under), "open": self.open,
                    **({"under": under} if under else {}),
                    **({"changes": [m["change"] for m in made]} if made else {}),
                    **({"working": fresh} if action == "open" and fresh else {}),
                    "note": "The general topic is always searched. A topic with topics under it "
                            "says how many; name it here to list them."}
        if name == "tapestry_file":
            ids = [self._memory_id(m) for m in args.get("memories") or []]
            add, remove = list(args.get("add") or []), list(args.get("remove") or [])
            if not ids or None in ids:
                return {"error": "memories must be #numbers; nothing was changed"}
            if not add and not remove:
                return {"error": "name topics to add, to remove, or both; nothing was changed"}
            if missing := self._unknown([*add, *remove]):
                return {"error": f"no topic {', '.join(missing)}; nothing was changed. "
                                 "tapestry_topics creates one."}
            # The same answer for a memory that doesn't exist as for one this session can't see.
            if hidden := [m for m in ids if not self.mind.visible(m, self.searched(remove))]:
                return {"error": f"no memory {', '.join(f'#{m}' for m in hidden)} in the topics searched; "
                                 "open the topic it is filed in, or name it in remove. Nothing was changed."}
            made = change.file(self.mind, ids, add=add, remove=remove, **self._logged(args))
            now = {m: self.mind.memory(m)["topics"] or ["general"] for m in ids}
            return {"filed": len(ids), "now_in_general": [m for m, t in now.items() if t == ["general"]],
                    **({"change": made["change"]} if made else {})}
        if name == "tapestry_associate":
            pair = [self._memory_id(m) for m in args.get("memories") or []]
            if len(pair) != 2 or None in pair:
                return {"error": "memories must be two #numbers; nothing was changed"}
            extra = list(args.get("topics") or [])
            if missing := self._unknown(extra):
                return {"error": f"no topic {', '.join(missing)}; nothing was changed"}
            if hidden := [m for m in pair if not self.mind.visible(m, self.searched(extra))]:
                return {"error": f"no memory {', '.join(f'#{m}' for m in hidden)} in the topics searched; "
                                 "if it's filed in another, name that topic or open it. Nothing was changed."}
            how = args.get("how") or "link"
            made = change.associate(self.mind, *pair, how, **self._logged(args))
            return {"associated": pair, "how": how, **made}
        if name == "tapestry_changes":
            mine = self._logged(args)
            if (args.get("action") or "list") == "undo":
                if isinstance(args.get("change"), int) and not isinstance(args.get("change"), bool):
                    try:
                        return {"undone": [args["change"]], **change.undo(self.mind, args["change"], **mine)}
                    except change.Stale as e:
                        return {"undone": [], "refused": [{"change": args["change"], "why": str(e)}]}
                if args.get("session"):
                    return change.undo_session(self.mind, args["session"], **mine)
                return {"error": "undo needs a change's number or a session; nothing was changed"}
            limit = args.get("limit") if isinstance(args.get("limit"), int) else 20
            return {"changes": [{**c, "at": render.ago(c["at"], time.time()), "reason": self._escape(c["reason"])}
                                for c in change.log(self.mind, limit=min(max(limit, 1), 100),
                                                    session=args.get("session") or None)],
                    "this_session": mine["session"]}
        if name == "tapestry_introspect":
            topic = (args.get("topic") or "").strip() or None
            if topic and self._unknown([topic]):
                return {"error": f"no topic {topic!r}; nothing was looked at"}
            limit = args.get("limit")
            found = introspect.digest(self.mind, section=args.get("section") or None, topic=topic,
                                      limit=min(max(int(limit), 1), 30) if isinstance(limit, int) else 10)
            return {**self._escaped(found),
                    "read": "every topic, open or not" if not topic else f"{topic} and what is under it",
                    "note": "Counted, not judged: each item is a place to look."}
        if name == "tapestry_working":
            action, now = args.get("action") or "list", time.time()
            shown = lambda w: self._escape(render.working_line(w, now))
            if action in ("rewrite", "remove"):
                ref = str(args.get("id") or "").strip().lower().removeprefix("w")
                if not (ref.isascii() and ref.isdigit()):
                    return {"error": f"{action} needs the working memory's handle, such as \"w3\"; nothing was changed"}
                if action == "remove":
                    working.remove(self.mind, int(ref), now=now)
                    return {"removed": f"w{ref}"}
                until = args.get("until")
                until = working.KEEP if until in (None, "") else None if str(until).strip().lower() == "never" else until
                return {"rewritten": shown(working.rewrite(self.mind, int(ref), args.get("content") or "",
                                                           until=until, now=now))}
            if action == "write":
                return {"written": shown(working.write(self.mind, args.get("content") or "", now=now,
                                                       topic=(args.get("topic") or "").strip() or None,
                                                       until=args.get("until") or None))}
            return {"working": [shown(w) for w in working.in_view(self.mind, self.open, now=now)],
                    "note": "The working memory of the general topic and the open ones. That of another "
                            "topic is in view when the topic is open."}
        return {"error": f"unknown tool {name}"}
