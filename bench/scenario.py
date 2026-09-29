"""Scenario runs: put a memory system's recall in front of a real agent, several
times over, and judge how the agent behaves.

Retrieval metrics say what a system served. Scenarios check what the agent
*did* with it: did it hedge on a weak memory, repeat a stale one, nag, or
leak another project's memory into this one? Behaviour varies from run to
run, so every scenario runs N times and reports a pass rate, not a verdict.

    python -m bench.scenario run                  # run, judge and report everything
    python -m bench.scenario run -s tapestry -n 5 stale-preference
    python -m bench.scenario judge [RUN_DIR]      # re-judge a run (default: latest)
    python -m bench.scenario report [RUN_DIR]     # pass rates, appended to results.jsonl

A scenario file (bench/scenarios/*.toml) holds seeded memories in the fixture
format, the user's turns, and plain-language assertions:

    name = "stale-preference"
    context = "You are Ash, Sam's personal assistant."   # optional
    namespaces = ["atlas"]                               # optional: open namespaces
    tools = true      # optional: give the agent tapestry's tools (tapestry system only)
    [[namespace]]     # optional: namespace descriptions
    name = "finance"
    description = "Money: budgets, salary, taxes."
    assertions = ["The assistant never calls pink Sam's current favorite color."]
    [[memory]]  ...
    [[turn]]
    user = "Grab me some balloons in my favorite color?"

With tools = true, the agent also gets tapestry's MCP server over the same
mind, launched from a project folder named after the open namespace, and the
transcript records every tool call and every memory the run added.

Both the agent and the judge are Claude Code in headless print mode, isolated
from this machine: no settings, CLAUDE.md, tools or MCP servers. Recall is
appended to each user message in the same format Hermes uses, so what the
agent sees matches what Ash sees. The judge sees the transcript and the
assertions, returns a structured verdict per assertion, and is strict:
partial compliance fails.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from bench import systems
from bench.fixture import Fixture, Memory, Query, _names, _when, descriptions

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "bench" / "scenarios"
TRANSCRIPTS = ROOT / "bench" / "transcripts"
LOG = ROOT / "bench" / "results.jsonl"
DEFAULT_CONTEXT = "You are a helpful personal assistant with long-term memory."
# Claude Code injects the operator's account email even in isolated print mode.
# Without this, agents build signatures and names from the harness owner's
# identity rather than the scenario's user.
HARNESS_NOTE = ("Any account details elsewhere in your context, such as an email "
                "address, belong to the test environment, not to the user you're "
                "talking with. Ignore them. Everything you know about the user comes "
                "from this prompt and your memory.")

JUDGE_PROMPT = """\
You judge an AI assistant's behaviour in one recorded conversation.

Each turn shows what the user typed, the message as the assistant received it
(any recalled memory is appended in a <memory-context> block), and the
assistant's reply. The memory block is not something the user said: it's what
the assistant's memory system recalled, and it may be stale, irrelevant or
wrong.

Judge only the assistant's replies. For each assertion, decide PASS or FAIL
and quote the reply as evidence. Be strict: partial compliance is FAIL, and
missing evidence is FAIL. Give a reason for every FAIL. Judge each assertion
on its own words; don't import requirements it doesn't state."""

VERDICT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {"assertions": {"type": "array", "items": {
        "type": "object",
        "properties": {"assertion": {"type": "string"},
                       "result": {"type": "string", "enum": ["PASS", "FAIL"]},
                       "evidence": {"type": "string"},
                       "reason": {"type": "string"}},
        "required": ["assertion", "result", "evidence"]}}},
    "required": ["assertions"]})


def hermes_memory_block(recalled: str) -> str:
    """Verbatim from hermes-agent agent/memory_manager.py build_memory_context_block."""
    return ("<memory-context>\n"
            "[System note: The following is recalled memory context, "
            "NOT new user input. Treat as authoritative reference data — "
            "this is the agent's persistent memory and should inform all responses.]\n\n"
            f"{recalled}\n"
            "</memory-context>")


@dataclass
class Scenario:
    name: str
    fixture: Fixture
    turns: list[str]
    assertions: list[str]
    context: str
    namespaces: frozenset[str]
    description: str
    tools: bool
    descriptions: dict[str, str]


def load(path: Path) -> Scenario:
    raw = tomllib.loads(Path(path).read_text())
    name = raw.get("name", Path(path).stem)
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError(f"scenario name {name!r} must be lowercase letters, digits and dashes")
    memories = [Memory(id=m["id"], content=m["content"], namespaces=_names(m),
                       at=_when(m.get("at")), supersedes=m.get("supersedes"),
                       tags=m.get("tags", ""))
                for m in raw.get("memory", [])]
    turns = [t["user"] for t in raw.get("turn", [])]
    if not turns or not raw.get("assertions"):
        raise ValueError(f"{name}: a scenario needs at least one turn and one assertion")
    namespaces = frozenset(raw.get("namespaces", ()))
    if raw.get("tools") and len(namespaces) > 1:
        raise ValueError(f"{name}: a tools scenario opens one namespace, its project")
    fx = Fixture(name=name, memories=memories,
                 queries=[Query(text=t, namespaces=namespaces) for t in turns])
    return Scenario(name=name, fixture=fx, turns=turns, assertions=list(raw["assertions"]),
                    context=raw.get("context", DEFAULT_CONTEXT), namespaces=namespaces,
                    description=raw.get("description", "").strip(),
                    tools=bool(raw.get("tools")), descriptions=descriptions(raw))


def load_all(names: list[str] | None = None) -> list[Scenario]:
    found = [load(p) for p in sorted(SCENARIOS.glob("*.toml"))]
    if names:
        missing = set(names) - {s.name for s in found}
        if missing:
            raise SystemExit(f"unknown scenario(s): {', '.join(sorted(missing))}")
        found = [s for s in found if s.name in names]
    return found


def _claude(extra: list[str], stdin: str, model: str, system_prompt: str,
            cwd: str | None = None) -> str:
    """Isolated headless Claude Code: no settings, CLAUDE.md or built-in tools, and
    no MCP servers beyond any passed in `extra`."""
    with tempfile.TemporaryDirectory(prefix="tapestry-scenario-") as tmp:
        proc = subprocess.run(
            ["claude", "-p", "--setting-sources", "", "--tools", "", "--strict-mcp-config",
             "--exclude-dynamic-system-prompt-sections", "--no-session-persistence",
             "--model", model, "--system-prompt", system_prompt, *extra],
            input=stdin, capture_output=True, text=True, cwd=cwd or tmp, timeout=900)
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[-500:]}")
    return proc.stdout


def _agent(system_prompt: str, user_messages: list[str], model: str, *,
           mcp: list[str] | None = None, cwd: str | None = None
           ) -> tuple[list[str], list[list[dict]], float]:
    """One multi-turn conversation. Returns the replies, each turn's tool calls,
    and the cost."""
    feed = "".join(json.dumps({"type": "user", "message": {"role": "user", "content": m}}) + "\n"
                   for m in user_messages)
    out = _claude(["--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", *(mcp or [])], feed, model, system_prompt, cwd=cwd)
    replies: list[str] = []
    calls: list[list[dict]] = []
    current: list[str] = []
    current_calls: list[dict] = []
    pending: dict[str, dict] = {}
    cost = 0.0
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for b in event["message"]["content"]:
                if b.get("type") == "text" and b.get("text"):
                    current.append(b["text"])
                elif b.get("type") == "tool_use":
                    call = {"tool": b.get("name"), "input": b.get("input")}
                    pending[b.get("id")] = call
                    current_calls.append(call)
        elif event.get("type") == "user":
            for b in (event.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    c = b.get("content")
                    pending[b["tool_use_id"]]["result"] = (
                        c if isinstance(c, str) else "".join(x.get("text", "") for x in c or []))
        elif event.get("type") == "result":
            if event.get("is_error"):
                raise RuntimeError(f"agent turn failed: {event.get('result')}")
            replies.append("\n".join(current))
            calls.append(current_calls)
            current, current_calls = [], []
            cost += event.get("total_cost_usd") or 0.0
    if len(replies) != len(user_messages):
        raise RuntimeError(f"expected {len(user_messages)} replies, got {len(replies)}")
    return replies, calls, cost


TOOL_NAMES = ["tapestry_recall", "tapestry_why", "tapestry_note", "tapestry_namespaces"]


def _mcp_setup(scn: Scenario, system) -> tuple[list[str], str]:
    """Point the real MCP server at the system's mind, from a project folder."""
    home = system.home
    project = next(iter(scn.namespaces), "project")
    cwd = home / "proj" / project
    (cwd / ".git").mkdir(parents=True, exist_ok=True)
    config = home / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"tapestry": {
        "command": str(ROOT / "bin" / "tapestry"), "args": ["mcp"],
        "env": {"TAPESTRY_HOME": str(home), "TAPESTRY_OWNER": "scenario",
                "TAPESTRY_PYTHON": sys.executable}}}}))
    allowed = ",".join(f"mcp__tapestry__{t}" for t in TOOL_NAMES)
    return ["--mcp-config", str(config), "--allowedTools", allowed], str(cwd)


def _max_memory_id(system) -> int:
    return system._mind.db.execute("SELECT coalesce(max(id), 0) FROM memories").fetchone()[0]


def _memories_after(system, since: int) -> list[dict]:
    rows = system._mind.db.execute(
        "SELECT m.id, m.content, (SELECT group_concat(n.name, ', ') FROM memory_namespaces x"
        " JOIN namespaces n ON n.id = x.namespace_id WHERE x.memory_id = m.id)"
        " FROM memories m WHERE m.id > ? ORDER BY m.id", (since,)).fetchall()
    return [{"content": c, "namespaces": ns or "default"} for _, c, ns in rows]


def supports_tools(system) -> bool:
    return getattr(system, "home", None) is not None or hasattr(type(system), "home")


def run_one(scn: Scenario, system, *, k: int, model: str) -> dict:
    fx = scn.fixture
    system.seed(fx.memories)
    if scn.descriptions and hasattr(system, "describe"):
        system.describe(scn.descriptions)
    contents = {m.id: m.content for m in fx.memories}
    searched = fx.searched(fx.queries[0])
    turns, messages = [], []
    for text in scn.turns:
        hits = system.recall(text, namespaces=searched, k=k, at=None)
        rendered = system.render(hits, contents) if hits else ""
        message = text + ("\n\n" + hermes_memory_block(rendered) if rendered else "")
        messages.append(message)
        turns.append({"user": text, "recalled": [h.__dict__ for h in hits], "sent": message})
    block = system.system_prompt_block()
    system_prompt = scn.context + "\n\n" + HARNESS_NOTE + ("\n\n" + block if block else "")
    mcp, cwd, before = (None, None, 0)
    if scn.tools:
        mcp, cwd = _mcp_setup(scn, system)
        before = _max_memory_id(system)
    replies, calls, cost = _agent(system_prompt, messages, model, mcp=mcp, cwd=cwd)
    for t, r, c in zip(turns, replies, calls):
        t["assistant"] = r
        if scn.tools:
            t["tool_calls"] = c
    out = {"scenario": scn.name, "description": scn.description, "assertions": scn.assertions,
           "system": system.name, "model": model, "k": k, "system_prompt": system_prompt,
           "turns": turns, "cost_usd": round(cost, 6)}
    if scn.tools:
        out["memories_added"] = _memories_after(system, before)
    return out


# -- judging -------------------------------------------------------------------

def judge_transcript(t: dict, model: str) -> dict:
    if "assertions" not in t:  # transcripts from before assertions were stored
        scn = load(SCENARIOS / f"{t['scenario']}.toml")
        t = {**t, "assertions": scn.assertions, "description": scn.description}
    def calls(turn):
        if "tool_calls" not in turn:
            return ""
        if not turn["tool_calls"]:
            return "\n\n**Assistant's tool calls:** none"
        return "\n\n**Assistant's tool calls:**\n" + "\n".join(
            f"- {c['tool']}({json.dumps(c.get('input'))}) -> {str(c.get('result', ''))[:600]}"
            for c in turn["tool_calls"])
    convo = "\n\n".join(
        f"### Turn {i}\n**User typed:** {turn['user']}\n\n**Assistant received:**\n{turn['sent']}"
        f"{calls(turn)}\n\n**Assistant replied:**\n{turn['assistant']}"
        for i, turn in enumerate(t["turns"], 1))
    if "memories_added" in t:
        added = t["memories_added"]
        convo += ("\n\n### Memories the assistant saved during the conversation\n"
                  + ("\n".join(f"- [{m['namespaces']}] {m['content']}" for m in added)
                     if added else "None."))
    prompt = (f"Scenario: {t.get('description') or t['scenario']}\n\nAssertions:\n"
              + "".join(f"{i}. {a}\n" for i, a in enumerate(t["assertions"], 1))
              + f"\nConversation:\n\n{convo}\n\nReturn one verdict per assertion, in order, "
                "with the assertion text copied exactly.")
    try:
        out = json.loads(_claude(["--output-format", "json", "--json-schema", VERDICT_SCHEMA],
                                 prompt, model, JUDGE_PROMPT))
        if out.get("is_error"):
            raise RuntimeError(str(out.get("result"))[:300])
        verdicts = out["structured_output"]["assertions"]
        if len(verdicts) != len(t["assertions"]):
            raise RuntimeError(f"judge returned {len(verdicts)} verdicts for "
                               f"{len(t['assertions'])} assertions")
        for v, a in zip(verdicts, t["assertions"]):
            v["assertion"] = a  # keyed by the scenario's text, not the judge's copy
        return {"result": "PASS" if all(v["result"] == "PASS" for v in verdicts) else "FAIL",
                "assertions": verdicts, "error": None,
                "judge_cost_usd": out.get("total_cost_usd")}
    except Exception as e:  # an unjudged run is reported as such, never as a pass
        return {"result": "ERROR", "assertions": [], "error": f"{type(e).__name__}: {e}"}


def _latest_run() -> Path:
    runs = sorted(p for p in TRANSCRIPTS.iterdir() if (p / "run.json").exists())
    if not runs:
        raise SystemExit("no scenario runs found; run: python -m bench.scenario run")
    return runs[-1]


def _transcripts(run_dir: Path) -> list[Path]:
    return sorted(p for p in run_dir.glob("*/*.json"))


def _write_junit(run_dir: Path, judged: dict[str, dict]) -> None:
    suites = ET.Element("testsuites")
    for name, j in sorted(judged.items()):
        verdicts = j["assertions"]
        suite = ET.SubElement(suites, "testsuite", name=name, tests=str(len(verdicts)),
                              failures=str(sum(v["result"] == "FAIL" for v in verdicts)),
                              errors="1" if j["result"] == "ERROR" else "0")
        if j["result"] == "ERROR":
            case = ET.SubElement(suite, "testcase", name="(evaluation)", classname=name)
            ET.SubElement(case, "error", message=j["error"]).text = j["error"]
        for v in verdicts:
            case = ET.SubElement(suite, "testcase", name=v["assertion"], classname=name)
            if v["result"] == "FAIL":
                reason = v.get("reason") or "assertion failed"
                ET.SubElement(case, "failure", message=reason).text = (
                    f"evidence: {v.get('evidence', '')}\nreason: {reason}")
    ET.indent(suites)
    ET.ElementTree(suites).write(run_dir / "judgments.xml", encoding="unicode",
                                 xml_declaration=True)


def cmd_judge(run_dir: Path, model: str, workers: int) -> int:
    paths = _transcripts(run_dir)
    loaded = [(p, json.loads(p.read_text())) for p in paths]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda pt: judge_transcript(pt[1], model), loaded))
    judged = {f"{t['scenario']}__{t['system']}__{p.stem.rsplit('-', 1)[-1]}": {**j, "path": str(p)}
              for (p, t), j in zip(loaded, results)}
    (run_dir / "judgments.json").write_text(json.dumps({"judge_model": model, "runs": judged},
                                                       indent=2))
    _write_junit(run_dir, judged)
    errors = [n for n, j in judged.items() if j["result"] == "ERROR"]
    print(f"judged {len(judged)} transcript(s) in {run_dir.relative_to(ROOT)}"
          + (f"; {len(errors)} errored: {', '.join(errors)}" if errors else ""))
    return 0


def cmd_report(run_dir: Path, log: bool) -> int:
    path = run_dir / "judgments.json"
    if not path.exists():
        print(f"{path.relative_to(ROOT)} not found; run: python -m bench.scenario judge",
              file=sys.stderr)
        return 2
    data = json.loads(path.read_text())
    meta = json.loads((run_dir / "run.json").read_text())
    table: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    whole: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    errors: dict[tuple[str, str], int] = defaultdict(int)
    for name, j in data["runs"].items():
        scn, sysname, _ = name.split("__")
        key = (scn, sysname)
        if j["result"] == "ERROR":
            errors[key] += 1
            continue
        for v in j["assertions"]:
            cell = table[key][v["assertion"]]
            cell[0] += v["result"] == "PASS"
            cell[1] += 1
        whole[key][0] += j["result"] == "PASS"
        whole[key][1] += 1
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or None
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = []
    for key in sorted(set(whole) | set(errors)):
        scn, sysname = key
        p, n = whole[key]
        err = f", {errors[key]} errored" if errors[key] else ""
        print(f"\n{sysname} / {scn}: {p}/{n} runs passed every assertion{err}")
        for assertion, (ap, an) in table[key].items():
            print(f"  {ap}/{an}  {assertion}")
        if n:
            lines.append({"ts": ts, "commit": commit, "kind": "scenario", "system": sysname,
                          "scenario": scn, "model": meta.get("model"),
                          "judge_model": data.get("judge_model"), "run": run_dir.name,
                          "metrics": {"runs": n, "errored": errors[key],
                                      "all_pass_rate": round(p / n, 4),
                                      "assertions": {a: round(x / y, 4)
                                                     for a, (x, y) in table[key].items()}}})
    if log and lines:
        with LOG.open("a") as fh:
            fh.writelines(json.dumps(line) + "\n" for line in lines)
        print(f"\nlogged {len(lines)} result(s) to {LOG.relative_to(ROOT)}")
    return 0


def cmd_run(args) -> int:
    scenarios = load_all(args.scenarios)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = TRANSCRIPTS / stamp
    total = 0.0
    for name in args.system or systems.NAMES:
        system = systems.get(name)
        try:
            for scn in scenarios:
                if scn.tools and not supports_tools(system):
                    print(f"  {name:<9} {scn.name:<24} skipped: no tools for this system",
                          file=sys.stderr)
                    continue
                for i in range(1, args.runs + 1):
                    t = run_one(scn, system, k=args.k, model=args.model)
                    path = out_dir / scn.name / f"{name}-r{i}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(t, indent=2))
                    total += t["cost_usd"]
                    print(f"  {name:<9} {scn.name:<24} run {i}/{args.runs}  ${t['cost_usd']:.4f}",
                          file=sys.stderr, flush=True)
        finally:
            system.close()
    (out_dir / "run.json").write_text(json.dumps(
        {"model": args.model, "runs": args.runs, "k": args.k,
         "systems": args.system or list(systems.NAMES),
         "scenarios": [s.name for s in scenarios], "agent_cost_usd": round(total, 4)}, indent=2))
    print(f"transcripts in {out_dir.relative_to(ROOT)}; agent cost ${total:.2f}")
    if args.no_judge:
        return 0
    cmd_judge(out_dir, args.judge_model, args.workers)
    return cmd_report(out_dir, log=not args.no_log)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run scenarios, then judge and report")
    r.add_argument("scenarios", nargs="*")
    r.add_argument("-s", "--system", action="append", choices=systems.NAMES)
    r.add_argument("-n", "--runs", type=int, default=3)
    r.add_argument("-k", type=int, default=5)
    r.add_argument("-m", "--model", default="sonnet", help="the agent's model")
    r.add_argument("--judge-model", default="sonnet")
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--no-judge", action="store_true")
    r.add_argument("--no-log", action="store_true")
    j = sub.add_parser("judge", help="judge a run's transcripts")
    j.add_argument("run_dir", nargs="?", type=Path)
    j.add_argument("--judge-model", default="sonnet")
    j.add_argument("--workers", type=int, default=6)
    rep = sub.add_parser("report", help="pass rates for a judged run")
    rep.add_argument("run_dir", nargs="?", type=Path)
    rep.add_argument("--no-log", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    run_dir = (args.run_dir or _latest_run()).resolve()
    if args.cmd == "judge":
        return cmd_judge(run_dir, args.judge_model, args.workers)
    return cmd_report(run_dir, log=not args.no_log)


if __name__ == "__main__":
    sys.exit(main())
