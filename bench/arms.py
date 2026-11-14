"""What the agent is told about its memory, in arms, for bench.scenario --arm (PLAN.md, D).

    python -m bench.arms ARM      # the MCP server, serving that arm's tool descriptions

An arm is the guide shown at session start, the tool descriptions, and the line that carries
hints before a prompt. `built` is what ships. The others move how-to-use-a-tool out of the guide
and into that tool's description, as triggers, and drop what the labels already say:

- draft: what memory is, that what's shown is a sample, how to weigh match against belief,
  when to note, and how topics are searched;
- minimal: the same without the paragraph on weighing, and with topics left to the topic list;
- lean: minimal, with one sentence on forgetting.

Nothing here changes what the tools do.
"""

from __future__ import annotations

import sys

from tapestry import guide, tools

_IS = ("You have long-term memory, tapestry. It keeps facts drawn from past conversations, not the "
       "conversations themselves.")
_SAMPLE = ("Before each prompt you're shown only the few memories that clearly bear on it, and hints at "
           "where more may be. That is a sample, not all that memory holds: when an answer could depend on "
           "what you know about the user or their work, look further, and follow a hint when it could matter.")
_WEIGH = ("Each memory carries two separate labels. Match is how well it fits the moment; belief is how "
          "likely it is still right, from who said it and how long since anything bore on it. A close match "
          "can be stale or wrong, so lean on a memory no harder than its belief supports, and when something "
          "that matters rests on an uncertain one, say you're assuming it or ask. When two memories "
          "disagree, show both rather than picking one.")
_NOTE = ("Lasting facts are drawn from each conversation after it ends. Note something yourself when it "
         "shouldn't wait: the user asks you to remember it, corrects something you recalled, or you reach a "
         "conclusion worth keeping.")
_TOPICS = ("Memories are filed in topics. The general topic, for what matters everywhere, is always "
           "searched; any other, for one person, subject or project, only when it's open or named.")

_FORGET = ("Forgetting is complete and can't be undone, so before anything is forgotten, show the user "
           "what would go and wait for them to confirm.")

GUIDES = {"built": guide.GUIDE,
          "draft": "\n\n".join([_IS, _SAMPLE, _WEIGH, _NOTE, _TOPICS]),
          "minimal": "\n\n".join([_IS, _SAMPLE, _NOTE]),
          "lean": "\n\n".join([_IS, _SAMPLE, _NOTE, _FORGET])}

# Descriptions that say when to call each tool, for the arms whose guide no longer does.
DESCRIPTIONS = {
    "tapestry_recall":
        "Search your long-term memory. Use it when an answer could depend on what you know about the "
        "user, the people around them or their work and what you were shown doesn't cover it; when a "
        "hint says more memories match or names a topic; and before telling the user you don't know "
        "something about them. It searches the general topic, the topics open in this session and any "
        "you name; a topic that isn't open is searched only when named. Results carry the same labels "
        "as the memories shown before a prompt. Nothing found means nothing in the topics searched, "
        "not that nothing is remembered.",
    "tapestry_why":
        "Show the evidence behind a memory: who said it, when, which topics it was filed under, and "
        "what has superseded it since. Use it when two memories disagree, or before relying on one "
        "for something that matters. A memory you recalled by naming a topic needs that topic named "
        "here too.",
    "tapestry_note":
        "Remember a fact now, rather than leaving it to be drawn after the conversation ends. Use it "
        "when the user asks you to remember something, when they correct or update something you "
        "recalled (pass that memory's #number as supersedes), or when you reach a conclusion worth "
        "knowing in a later conversation. Write one fact as a standalone statement that makes sense "
        "without this conversation, and file it under the topics where it matters. Not for what only "
        "matters to this conversation. If your host has a memory of its own, prefer this one: a fact "
        "kept only there has no source, topic or belief.",
    "tapestry_record_answer":
        "Record the user's reply when you asked them whether a memory is still true: confirmed, or "
        "contradicted. Call it as soon as they answer. Only for a real answer from the user: not for "
        "silence, and never for your own use or repetition of a memory. If they contradict it and "
        "say what is true now, also save that with tapestry_note, passing this memory as supersedes.",
    "tapestry_topics":
        "List every topic with its description (never its contents), open or close one for the rest "
        "of this session, or create one and describe it. Create a topic when a new person, subject "
        "or project keeps coming up, with a description of what it will hold so it can be found "
        "without opening it.",
}
_HOW = " If they could matter, look with tapestry_recall, naming any topic that isn't open."


def hint(arm: str, automatic: str) -> str:
    """The text shown before a prompt. Outside `built`, a hint says where more is and leaves
    how to look to the tool's own description."""
    return automatic if arm == "built" else automatic.replace(_HOW, "")


def schemas(arm: str) -> list[dict]:
    if arm == "built":
        return tools.SCHEMAS
    return [{**s, "description": DESCRIPTIONS.get(s["name"], s["description"])} for s in tools.SCHEMAS]


if __name__ == "__main__":
    tools.SCHEMAS[:] = schemas(sys.argv[1])
    from tapestry.mcp_server import serve
    serve()
