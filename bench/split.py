"""Split drawing: Claude reads the conversation and writes the facts; Jev decides the rest.

    python -m bench.extract fixtures --drawer split [...]

Drawing as built asks one model for everything about a fact in one answer. Here the model that
reads the conversation gives only what takes reading it: the fact, the turns it came from and who
said it. What can be decided from the fact alone, set beside a topic or a known memory, is a
bounded question, and goes to Jev: where the fact is filed, how quickly it goes stale, and which
known memory it repeats or replaces.

Jev is sent drawn facts, topic names and descriptions, and known memories, never a turn of the
conversation. It can't name a topic, so this arm files only under topics that exist, and it
doesn't say what a fact builds on.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tapestry import judge as judges

# What the model reading the conversation is asked: the drawer's own words, up to where the
# bounded decisions begin, so the arms differ in structure and not in wording.
SYSTEM = judges._DRAW_SYSTEM[:judges._DRAW_SYSTEM.index("- topics:")].rstrip()
SCHEMA = {"type": "object", "required": ["facts"], "properties": {"facts": {"type": "array", "items": {
    "type": "object", "required": ["content", "said_by", "turns"], "properties": {
        "content": {"type": "string"},
        "said_by": {"type": "string", "enum": ["user", "agent", "inferred"]},
        "turns": {"type": "array", "items": {"type": "integer"}}}}}}}

# Every cut-off is the even chance, set once and not tuned.
P = 0.5
BATCH = 20   # questions a request carries at most

GENERAL = "general: what matters in every context, whatever the conversation is about"
FILE = {"question": "Does this message belong under `topic`?",
        "criteria": {"true": "The message is a fact about the person, subject or project the topic covers",
                     "false": "The topic covers something else, or the message only touches it in passing"}}
SAME = {"question": "Does `memory` already say what this message says?",
        "criteria": {"true": "The memory states the same fact, in the same or other words",
                     "false": "The memory says something else, says less, or gives a different value"}}
REPLACES = {"question": "Does this message make `memory` out of date?",
            "criteria": {"true": "Both are about the very same thing, and the message gives a newer or corrected "
                                 "value for it, such as an amount, a count, a day or a place",
                         "false": "They are about different things, or the message adds something beside the "
                                  "memory without changing what it says"}}
VOLATILITY = {
    "immutable": "something that never changes, such as a name or a date of birth",
    "stable": "something that rarely changes, such as where someone lives or works",
    "preference": "what someone likes, wants or chooses",
    "project": "a plan, a decision or the state of some work",
    "transient": "what is in progress right now and will be over within days"}
KIND = {"question": "Is this message `kind`?",
        "criteria": {"true": "That describes the message better than any other kind of fact would",
                     "false": "The message is some other kind of fact"}}


def _noul(q: dict, name: str, value: str) -> dict:
    return {"type": "noul", "criteria": q["criteria"], "instructions": {name: value, "question": q["question"]}}


class SplitDrawer(judges.ClaudeDrawer):
    prompt = "draw_split_v1"
    system = SYSTEM

    def __init__(self, model: str = "haiku", *, jev: judges.JevJudge | None = None, **kw) -> None:
        super().__init__(model, **kw)
        self._jev = jev or judges.JevJudge(Path.home() / ".tapestry" / "jev.key")
        self._jev_costs: list[float] = []
        send = self._jev._send

        def counted(body: dict) -> dict:
            resp = send(body)
            gateway = (resp.get("provider_metadata") or {}).get("gateway") or {}
            self._jev_costs.append(float(gateway.get("cost") or 0))
            return resp
        self._jev._send = counted

    def _write(self, date: str, turns: list[tuple[int, str, str]]) -> list[dict]:
        """What only reading the conversation gives: each fact, its turns and who said it."""
        budget = 1 + len(turns) // self.turns_per_fact if self.turns_per_fact else None
        schema = SCHEMA if budget is None else {**SCHEMA, "properties": {"facts": {
            **SCHEMA["properties"]["facts"], "maxItems": budget}}}
        argv = ["claude", "-p", "--safe-mode", "--model", self.alias, "--tools", "", "--no-session-persistence",
                "--output-format", "json", "--system-prompt", self.system, "--json-schema", json.dumps(schema)]
        if self.effort:
            argv += ["--effort", self.effort]
        message = "\n\n".join([
            f"The conversation took place on {date}." if date else "Each turn is marked with when it was said.",
            "The conversation:\n" + "\n\n".join(f"[{i}] {who}: {text}" for i, who, text in turns),
            *([f"Write at most {budget} facts: the ones most worth knowing later. Fewer is fine."] if budget else [])])
        out = json.loads(self._run(argv, message))
        if out.get("is_error"):
            raise RuntimeError(f"claude reported an error: {str(out.get('result'))[:200]}")
        used = list((out.get("modelUsage") or {}))
        self.model = f"claude:{used[0]}+{judges.MODEL}" if used else self.model
        self.last["cost_usd"] = out.get("total_cost_usd")
        self.last["output_tokens"] = (out.get("usage") or {}).get("output_tokens")
        facts = (out.get("structured_output") or {}).get("facts")
        if not isinstance(facts, list):
            raise RuntimeError("claude gave no list of facts")
        return facts if budget is None else facts[:budget]

    def _decide(self, content: str, topics: list[dict], known: list[tuple[int, str]]) -> dict:
        """Where one fact is filed, how fast it goes stale, and what it repeats or replaces."""
        asked = ([("topic", GENERAL, _noul(FILE, "topic", GENERAL))]
                 + [("topic", t["name"], _noul(FILE, "topic", f"{t['name']}: {t['description'] or t['name']}"))
                    for t in topics]
                 + [("kind", k, _noul(KIND, "kind", d)) for k, d in VOLATILITY.items()]
                 + [("same", i, _noul(SAME, "memory", text)) for i, text in known]
                 + [("replaces", i, _noul(REPLACES, "memory", text)) for i, text in known])
        ps: list[float] = []
        for at in range(0, len(asked), BATCH):
            part = asked[at:at + BATCH]
            ps += self._jev._answers({"model": judges.MODEL, "state": {"message": content},
                                      "questions": {f"c:{i}": q for i, (_, _, q) in enumerate(part)},
                                      "providerOptions": {"gateway": {"only": [judges.PROVIDER]}}}, len(part))
        by = {"topic": {}, "kind": {}, "same": {}, "replaces": {}}
        for (what, key, _), p in zip(asked, ps):
            by[what][key] = p
        best = lambda d: max(d, key=d.get) if d and max(d.values()) >= P else None
        same = best(by["same"])
        if same is not None:
            return {"same_as": same}
        out = {"topics": [("general" if n == GENERAL else n) for n, p in by["topic"].items() if p >= P],
               "volatility": max(by["kind"], key=by["kind"].get)}
        if (old := best(by["replaces"])) is not None:
            out["replaces"] = old
        return out

    def draw(self, *, place: str | None, date: str, topics: list[dict], known: list[tuple[int, str]],
             turns: list[tuple[int, str, str]]) -> dict:
        t0 = time.monotonic()
        self.last = {}
        self._jev_costs.clear()
        facts = self._write(date, turns)
        t1 = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            decided = list(pool.map(lambda f: self._decide(str(f.get("content") or ""), topics, known), facts))
        self.last.update(claude_cost_usd=self.last["cost_usd"], jev_cost_usd=sum(self._jev_costs),
                         jev_requests=len(self._jev_costs),
                         cost_usd=(self.last["cost_usd"] or 0) + sum(self._jev_costs))
        self.last.update(wall_s=time.monotonic() - t0, write_s=t1 - t0, decide_s=time.monotonic() - t1,
                         model=self.model)
        return {"facts": [{**f, **d} for f, d in zip(facts, decided)]}
