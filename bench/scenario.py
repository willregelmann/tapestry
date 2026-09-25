"""Scenario runs: put a memory system's recall in front of a real agent, several
times over, and have fuzzy-assertions judge how the agent behaves.

Retrieval metrics say what a system served. Scenarios check what the agent
*did* with it: did it hedge on a weak memory, repeat a stale one, nag, or
leak another project's memory into this one? Behaviour varies from run to
run, so every scenario runs N times and reports a pass rate, not a verdict.

    python -m bench.scenario run                  # every scenario, every system
    python -m bench.scenario run -s mnemonic -n 5 stale-preference
    # then, in Claude Code in this repo:  /test:run --all
    python -m bench.scenario report               # pass rates from test-results.xml

A scenario file (bench/scenarios/*.toml) holds seeded memories in the fixture
format, the user's turns, and plain-language assertions:

    name = "stale-preference"
    context = "You are Ash, Sam's personal assistant."   # optional
    scopes = ["user"]                                    # optional: loaded scopes
    assertions = ["The assistant never calls pink Sam's current favorite color."]
    [[memory]]  ...
    [[turn]]
    user = "Grab me some balloons in my favorite color?"

The agent is Claude Code in headless print mode, isolated from this machine:
no settings, CLAUDE.md, tools or MCP servers. Recall is added to each user
message in the same format Hermes uses, so what the agent sees matches what
Ash sees.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from bench import systems
from bench.fixture import Fixture, Memory, Query, _when

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "bench" / "scenarios"
TRANSCRIPTS = ROOT / "bench" / "transcripts"
TESTS = ROOT / ".claude" / "tests"
TEST_PREFIX = "scn__"
RESULTS = ROOT / "test-results.xml"
LOG = ROOT / "bench" / "results.jsonl"
DEFAULT_CONTEXT = "You are a helpful personal assistant with long-term memory."
# Claude Code injects the operator's account email even in isolated print mode.
# Without this, agents build signatures and names from the harness owner's
# identity rather than the scenario's user.
HARNESS_NOTE = ("Any account details elsewhere in your context, such as an email "
                "address, belong to the test environment, not to the user you're "
                "talking with. Ignore them. Everything you know about the user comes "
                "from this prompt and your memory.")


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
    scopes: frozenset[str] | None
    description: str


def load(path: Path) -> Scenario:
    raw = tomllib.loads(Path(path).read_text())
    name = raw.get("name", Path(path).stem)
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError(f"scenario name {name!r} must be lowercase letters, digits and dashes")
    memories = [Memory(id=m["id"], content=m["content"], scope=m.get("scope", "user"),
                       at=_when(m.get("at")), supersedes=m.get("supersedes"),
                       tags=m.get("tags", ""))
                for m in raw.get("memory", [])]
    turns = [t["user"] for t in raw.get("turn", [])]
    if not turns or not raw.get("assertions"):
        raise ValueError(f"{name}: a scenario needs at least one turn and one assertion")
    scopes = frozenset(raw["scopes"]) if "scopes" in raw else None
    fx = Fixture(name=name, memories=memories,
                 queries=[Query(text=t, scopes=scopes) for t in turns])
    return Scenario(name=name, fixture=fx, turns=turns, assertions=list(raw["assertions"]),
                    context=raw.get("context", DEFAULT_CONTEXT), scopes=scopes,
                    description=raw.get("description", "").strip())


def load_all(names: list[str] | None = None) -> list[Scenario]:
    found = [load(p) for p in sorted(SCENARIOS.glob("*.toml"))]
    if names:
        missing = set(names) - {s.name for s in found}
        if missing:
            raise SystemExit(f"unknown scenario(s): {', '.join(sorted(missing))}")
        found = [s for s in found if s.name in names]
    return found


def _agent(system_prompt: str, user_messages: list[str], model: str) -> tuple[list[str], float]:
    """One isolated multi-turn conversation. Returns the replies and the cost."""
    feed = "".join(json.dumps({"type": "user", "message": {"role": "user", "content": m}}) + "\n"
                   for m in user_messages)
    with tempfile.TemporaryDirectory(prefix="tapestry-scenario-") as cwd:
        proc = subprocess.run(
            ["claude", "-p", "--setting-sources", "", "--tools", "", "--strict-mcp-config",
             "--exclude-dynamic-system-prompt-sections", "--no-session-persistence",
             "--model", model, "--system-prompt", system_prompt,
             "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
            input=feed, capture_output=True, text=True, cwd=cwd, timeout=600)
    replies: list[str] = []
    current: list[str] = []
    cost = 0.0
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            current += [b["text"] for b in event["message"]["content"]
                        if b.get("type") == "text" and b.get("text")]
        elif event.get("type") == "result":
            if event.get("is_error"):
                raise RuntimeError(f"agent turn failed: {event.get('result')}")
            replies.append("\n".join(current))
            current = []
            cost += event.get("total_cost_usd") or 0.0
    if len(replies) != len(user_messages):
        raise RuntimeError(f"expected {len(user_messages)} replies, got {len(replies)}; "
                           f"stderr: {proc.stderr[-500:]}")
    return replies, cost


def run_one(scn: Scenario, system, *, k: int, model: str) -> dict:
    fx = scn.fixture
    system.seed(fx.memories)
    contents = {m.id: m.content for m in fx.memories}
    loaded = fx.loaded_scopes(fx.queries[0])
    turns, messages = [], []
    for text in scn.turns:
        hits = system.recall(text, scopes=loaded, k=k, at=None)
        rendered = system.render(hits, contents) if hits else ""
        message = text + ("\n\n" + hermes_memory_block(rendered) if rendered else "")
        messages.append(message)
        turns.append({"user": text, "recalled": [h.__dict__ for h in hits], "sent": message})
    block = system.system_prompt_block()
    system_prompt = scn.context + "\n\n" + HARNESS_NOTE + ("\n\n" + block if block else "")
    replies, cost = _agent(system_prompt, messages, model)
    for t, r in zip(turns, replies):
        t["assistant"] = r
    return {"scenario": scn.name, "system": system.name, "model": model, "k": k,
            "system_prompt": system_prompt, "turns": turns, "cost_usd": round(cost, 6)}


def _write_test(scn: Scenario, system_name: str, run: int, transcript: Path) -> None:
    name = f"{TEST_PREFIX}{scn.name}__{system_name}__r{run}"
    d = TESTS / name
    d.mkdir(parents=True, exist_ok=True)
    rel = transcript.relative_to(ROOT)
    header = "---\n" + f"name: {name}\n" + "assertions:\n" + "".join(
        f"  - {json.dumps(a)}\n" for a in scn.assertions) + "---\n"
    body = f"""
Read the transcript at `{rel}` (relative to the project root). It's one
conversation between a user and an assistant, as JSON. Each turn holds:
- `user`: what the user typed;
- `sent`: the user's message as the assistant actually received it, with any
  recalled memory appended in a `<memory-context>` block;
- `assistant`: the assistant's reply.

The memory block is not something the user said. It's what the assistant's
memory system recalled, and it may be stale, irrelevant or wrong. Judge only
the assistant's replies against each assertion, and quote the reply as
evidence.

Scenario: {scn.description or scn.name}
"""
    (d / "test.md").write_text(header + body)


def cmd_run(args) -> int:
    scenarios = load_all(args.scenarios)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = TRANSCRIPTS / stamp
    if TESTS.exists():
        for old in TESTS.glob(f"{TEST_PREFIX}*"):
            shutil.rmtree(old)
    total = 0.0
    for name in args.system or systems.NAMES:
        system = systems.get(name)
        try:
            for scn in scenarios:
                for i in range(1, args.runs + 1):
                    t = run_one(scn, system, k=args.k, model=args.model)
                    path = out_dir / scn.name / f"{name}-r{i}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(t, indent=2))
                    _write_test(scn, name, i, path)
                    total += t["cost_usd"]
                    print(f"  {name:<9} {scn.name:<24} run {i}/{args.runs}  ${t['cost_usd']:.4f}",
                          file=sys.stderr, flush=True)
        finally:
            system.close()
    (out_dir / "run.json").write_text(json.dumps(
        {"model": args.model, "runs": args.runs, "k": args.k,
         "systems": args.system or list(systems.NAMES),
         "scenarios": [s.name for s in scenarios], "cost_usd": round(total, 4)}, indent=2))
    print(f"\ntranscripts in {out_dir.relative_to(ROOT)}; agent cost ${total:.2f}")
    print("next: run /test:run --all in Claude Code here, then: python -m bench.scenario report")
    return 0


def cmd_report(args) -> int:
    if not RESULTS.exists():
        print(f"{RESULTS.name} not found; run /test:run --all first", file=sys.stderr)
        return 2
    runs = sorted(p for p in TRANSCRIPTS.iterdir() if (p / "run.json").exists())
    meta = json.loads((runs[-1] / "run.json").read_text()) if runs else {}
    # (scenario, system) -> assertion -> [pass, total]; plus whole-run passes
    table: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    whole: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    errors = 0
    for suite in ET.parse(RESULTS).getroot().iter("testsuite"):
        m = re.fullmatch(rf"{TEST_PREFIX}(.+)__(.+)__r(\d+)", suite.get("name", ""))
        if not m:
            continue
        key = (m.group(1), m.group(2))
        if suite.get("errors") not in (None, "0"):
            errors += 1
            continue
        ok_run = True
        for case in suite.iter("testcase"):
            passed = case.find("failure") is None
            cell = table[key][case.get("name")]
            cell[0] += passed
            cell[1] += 1
            ok_run &= passed
        whole[key][0] += ok_run
        whole[key][1] += 1
    if not whole:
        print("no scenario results in test-results.xml", file=sys.stderr)
        return 2
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip() or None
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = []
    for (scn, sysname), cells in sorted(table.items()):
        p, n = whole[(scn, sysname)]
        print(f"\n{sysname} / {scn}: {p}/{n} runs passed every assertion")
        for assertion, (ap, an) in cells.items():
            print(f"  {ap}/{an}  {assertion}")
        lines.append({"ts": ts, "commit": git, "kind": "scenario", "system": sysname,
                      "scenario": scn, "model": meta.get("model"),
                      "metrics": {"runs": n, "all_pass_rate": round(p / n, 4),
                                  "assertions": {a: round(x / y, 4) for a, (x, y) in cells.items()}}})
    if errors:
        print(f"\n{errors} evaluation(s) errored and were excluded", file=sys.stderr)
    if not args.no_log:
        with LOG.open("a") as fh:
            fh.writelines(json.dumps(line) + "\n" for line in lines)
        print(f"\nlogged {len(lines)} result(s) to {LOG.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run scenarios and write transcripts and tests")
    r.add_argument("scenarios", nargs="*")
    r.add_argument("-s", "--system", action="append", choices=systems.NAMES)
    r.add_argument("-n", "--runs", type=int, default=3)
    r.add_argument("-k", type=int, default=5)
    r.add_argument("-m", "--model", default="sonnet")
    rep = sub.add_parser("report", help="pass rates from fuzzy-assertions' test-results.xml")
    rep.add_argument("--no-log", action="store_true")
    args = ap.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
