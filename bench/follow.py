"""Does the agent follow hints? The automatic path in a real agent loop (docs/capabilities/RECALL.md).

    python -m bench.follow [-n PER_TYPE] [--arms follow hints inject] [--runs N]

The crowded LongMemEval mind from `bench.extract crowd`: every picked question's sessions drawn
into one mind, so the other questions' facts and topics are the distractors. Each question is
then asked of a real headless agent, which gets exactly what Claude Code's hooks would give it
in a fresh project with no other topic open, and answers. Arms:

- follow: the session start and the prompt's highlights and hints, with the tapestry tools, so
  the agent can look further when it judges a hint worth following;
- hints: the same context without the tapestry tools, as when their server failed to start, so
  the highlights alone. The agent keeps an unrelated tool, as in any real host: with no tools at
  all it can write tool calls as text until it runs out, which no host would see;
- inject: every topic's top matches put in front of it with no tools, the view the automatic
  path deliberately doesn't give.

An isolated judge decides from the reply alone whether it gives the correct answer; a judge
that errors is reported as an error, never as a pass. Against correctness, what each arm spends:
characters put in front of the agent automatically, characters its own lookups brought back,
and how often it looked at all. Each question's agent works on its own copy of the mind.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bench import longmemeval as lme
from bench.extract import RESULTS, _crowd_mind, _pick
from bench.scenario import DEFAULT_CONTEXT, HARNESS_NOTE, ROOT, _agent, _claude
from tapestry import judge as judges, render
from tapestry.hosts import claude_code as cc
from tapestry.mind import Mind

ARMS = ("follow", "hints", "inject")
OWNER = "bench"
PROJECT = "assistant"   # the session's place: a folder with nothing filed under it
TOOLS = ("tapestry_recall", "tapestry_why", "tapestry_topics")
INJECT_K = 5
UNRELATED = "Read"      # a host always has tools of its own besides tapestry's

CORRECT_SYSTEM = ("Say whether the assistant's reply gives the correct answer to the question. Strict: a stale "
                  "value, a different value, a hedge between values or a refusal is false. Wording may differ.")
CORRECT_SCHEMA = json.dumps({"type": "object", "required": ["correct"],
                             "properties": {"correct": {"type": "boolean"}}})


def _correct(item: dict, reply: str, judge_model: str) -> bool | str:
    """Whether `reply` gives the question's answer, or an error string. Never a guess."""
    prompt = (f"Question (asked {item['question_date']}): {item['question']}\nCorrect answer: {item['answer']}"
              f"\n\nAssistant's reply:\n{reply or '(empty)'}")
    try:
        out = json.loads(_claude(["--output-format", "json", "--json-schema", CORRECT_SCHEMA],
                                 prompt, judge_model, CORRECT_SYSTEM))
        if out.get("is_error"):
            raise RuntimeError(str(out.get("result"))[:200])
        return bool(out["structured_output"]["correct"])
    except Exception as e:
        return f"ERROR {type(e).__name__}: {e}"


def _reminder(text: str) -> str:
    return f"<system-reminder>\n{text}\n</system-reminder>"


def _setup(item: dict, mind_db: Path, tmp: Path) -> dict:
    """A home holding a copy of the crowded mind, a project folder, and what Claude Code's hooks
    put in front of the agent there: the session start, and the prompt's context."""
    home = tmp / "home"
    (home / "minds").mkdir(parents=True)
    shutil.copy(mind_db, home / "minds" / f"{OWNER}.db")
    cwd = tmp / PROJECT
    (cwd / ".git").mkdir(parents=True)
    os.environ |= {"TAPESTRY_HOME": str(home), "TAPESTRY_OWNER": OWNER}
    start = cc.on_session_start({"cwd": str(cwd)})["hookSpecificOutput"]["additionalContext"]
    prompt = cc.on_prompt({"cwd": str(cwd), "prompt": item["question"]})
    prompt = prompt["hookSpecificOutput"]["additionalContext"] if prompt else ""
    mind = cc.open_mind(str(cwd))
    try:
        every = [t["name"] for t in mind.topics()]
        hits = mind.recall(item["question"], topics=every, k=INJECT_K)
        inject = "From long-term memory:\n" + render.budgeted(hits, render.PROMPT_BUDGET) if hits else ""
    finally:
        mind.close()
    config = tmp / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"tapestry": {
        "command": str(ROOT / "bin" / "tapestry"), "args": ["mcp"],
        "env": {"TAPESTRY_HOME": str(home), "TAPESTRY_OWNER": OWNER, "TAPESTRY_PYTHON": sys.executable}}}}))
    return {"cwd": str(cwd), "start": start, "prompt": prompt, "inject": inject,
            "mcp": ["--mcp-config", str(config), "--allowedTools", ",".join(f"mcp__tapestry__{t}" for t in TOOLS)]}


def _ask(item: dict, arm: str, s: dict, model: str) -> dict:
    today = lme._date(item["question_date"]).strftime("%A, %B %-d, %Y")
    system = f"{DEFAULT_CONTEXT}\n\n{HARNESS_NOTE}\n\nToday is {today}."
    context = s["inject"] if arm == "inject" else s["prompt"]
    if arm != "inject":
        system += "\n\n" + _reminder("SessionStart hook additional context: " + s["start"])
    message = item["question"] + ("\n\n" + _reminder("UserPromptSubmit hook additional context: " + context)
                                  if context else "")
    try:
        replies, calls, cost = _agent(system, [message], model, mcp=s["mcp"] if arm == "follow" else None,
                                      cwd=s["cwd"], tools=UNRELATED)
    except Exception as e:
        return {"error": f"ERROR asking: {type(e).__name__}: {e}"}
    calls = calls[0]
    return {"reply": replies[0], "cost_usd": cost, "shown": len(context),
            "calls": [{"tool": c["tool"], "input": c["input"]} for c in calls],
            "looked": sum(1 for c in calls if c["tool"].endswith("tapestry_recall")),
            "brought": sum(len(c.get("result") or "") for c in calls),
            "results": "\n".join(c.get("result") or "" for c in calls)}


def cmd(n: int, types: list[str], arms: list[str], model: str, drawer_model: str, effort: str | None,
        judge_model: str, runs: int, workers: int, log: bool) -> int:
    picked = _pick(types, n)
    drawer = judges.ClaudeDrawer(drawer_model, **({"effort": effort} if effort else {}))
    mind_db, manifest = _crowd_mind(picked, drawer, redraw=False)
    from tapestry import embed
    mind = Mind(mind_db, encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
    try:
        answer_mids = {}
        for item in picked:
            keys = [f"{sid}:{i}" for sid, s in zip(item["haystack_session_ids"], item["haystack_sessions"])
                    for i, t in enumerate(s) if t.get("has_answer")]
            answer_mids[item["question_id"]] = {r[0] for r in mind.db.execute(
                f"SELECT d.memory_id FROM drawn_from d JOIN memories m ON m.id = d.memory_id"
                f" WHERE m.superseded_by IS NULL AND d.turn_key IN ({','.join('?' * len(keys))})", keys)}
    finally:
        mind.close()
    with tempfile.TemporaryDirectory(prefix="tapestry-follow-") as root:
        setups = {}
        saved = {k: os.environ.get(k) for k in ("TAPESTRY_HOME", "TAPESTRY_OWNER")}
        try:
            for item in picked:     # the hooks read the home from the environment, so one at a time
                setups[item["question_id"]] = _setup(item, mind_db, Path(root) / item["question_id"])
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        jobs = [(item, arm, r) for item in picked for arm in arms for r in range(runs)
                if not any(s in manifest["errors"] for s in item["haystack_session_ids"])]
        with ThreadPoolExecutor(workers) as pool:
            asked = list(pool.map(lambda j: _ask(j[0], j[1], setups[j[0]["question_id"]], model), jobs))
            verdicts = list(pool.map(lambda ja: ja[1].get("error") or _correct(ja[0][0], ja[1]["reply"], judge_model),
                                     zip(jobs, asked)))
    rows = []
    for (item, arm, r), a, v in zip(jobs, asked, verdicts):
        mids = answer_mids[item["question_id"]]
        found = bool(mids) and any(re.search(rf"#{m}\b", a.get("results", "")) for m in mids)
        rows.append({"id": item["question_id"], "type": item["question_type"], "arm": arm, "run": r,
                     "correct": v, "found": found, **{k: a[k] for k in a if k != "results"}})
        print(f"{item['question_id']} [{item['question_type']}] {arm}: correct={v} looked={a.get('looked')} "
              f"found={found} calls={[c['input'] for c in a.get('calls', [])]}", flush=True)

    def mean(group, f):
        return round(sum(f(x) for x in group) / len(group), 3) if group else None
    summary = {"mind": mind_db.name, "model": model, "questions": len(picked), "runs": runs,
               "errors": sum(1 for x in rows if isinstance(x["correct"], str)), "arms": {}}
    for arm in arms:
        group = [x for x in rows if x["arm"] == arm and "error" not in x]
        summary["arms"][arm] = {
            "correct": mean(group, lambda x: x["correct"] is True),
            "looked": mean(group, lambda x: x["looked"] > 0),
            "found_when_looked": mean([x for x in group if x["looked"]], lambda x: x["found"]),
            "lookups": mean(group, lambda x: x["looked"]),
            "shown": mean(group, lambda x: x["shown"]),
            "brought": mean(group, lambda x: x["brought"]),
            "cost_usd": round(sum(x["cost_usd"] for x in group), 3),
            "by_type": {t: mean([x for x in group if x["type"] == t], lambda x: x["correct"] is True)
                        for t in types}}
    print(json.dumps({k: v for k, v in summary.items() if k != "arms"}, indent=1))
    print(f"{'arm':<8}{'correct':>9}{'looked':>8}{'found':>7}{'lookups':>9}{'shown':>7}{'brought':>9}{'cost':>7}")
    for arm, m in summary["arms"].items():
        print(f"{arm:<8}" + "".join(f"{'-' if m[k] is None else format(m[k], f):>{w}}" for k, w, f in
                                    (("correct", 9, ".2f"), ("looked", 8, ".2f"), ("found_when_looked", 7, ".2f"),
                                     ("lookups", 9, ".1f"), ("shown", 7, ".0f"), ("brought", 9, ".0f"),
                                     ("cost_usd", 7, ".2f"))))
        print(f"{'':<8}  " + " ".join(f"{t}={v}" for t, v in m["by_type"].items()))
    if log:
        with open(RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "follow", "drawer": drawer_model, "judge_model": judge_model,
                                 "metrics": summary, "rows": rows}) + "\n")
    return 1 if summary["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.follow", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--per-type", dest="per_type", type=int, default=10, help="questions per type")
    ap.add_argument("--types", nargs="+", default=["single-session-user", "knowledge-update", "multi-session"])
    ap.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    ap.add_argument("--model", default="sonnet", help="the agent's model")
    ap.add_argument("--drawer-model", default="haiku", help="the drawer the crowded mind was drawn with")
    ap.add_argument("--effort", default=None, help="the drawer's effort; the drawer's default when omitted")
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--runs", type=int, default=1, help="runs per question and arm")
    ap.add_argument("--workers", type=int, default=6, help="agents at once")
    ap.add_argument("--log", action="store_true", help="append the result to bench/results.jsonl")
    a = ap.parse_args(argv)
    return cmd(a.per_type, a.types, a.arms, a.model, a.drawer_model, a.effort, a.judge_model, a.runs,
               a.workers, a.log)


if __name__ == "__main__":
    sys.exit(main())
