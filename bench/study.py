"""Learning a subject, two ways: an A/B of how a mind should learn from sources (PLAN.md, G).

    python -m bench.study questions SUBJECT            # write the question set from the reference sources
    python -m bench.study learn  SUBJECT ARM [--run N]  # one learning session into a fresh mind
    python -m bench.study ask    SUBJECT [ARM:RUN ...]  # answer every question from each mind, and closed-book
    python -m bench.study report [--log]

Both arms are the same agent (Sonnet, headless Claude Code) told the same thing: learn about
SUBJECT so later conversations can answer questions about it without looking it up. Both have
web search and fetch, tapestry's recall, note, why and topics, and the same spending limit.

- read: the design. A tapestry_read tool takes a source the agent chose; after the session the
  source is read passage by passage by Haiku (bench.learn's page drawer, low effort, no budget),
  each fact citing the source.
- note: a skill. The agent reads what it chooses and notes what it judges worth keeping with
  tapestry_note, as the agent would with no reading pipeline.

The subjects postdate the agent's training, so answers can only come from what was learned; a
closed-book arm measures how much the agent knows anyway. A fresh agent answers each question with
only what Claude Code's hooks and tapestry's recall tools give it, in a session whose place is the
subject's topic, and an isolated judge grades the reply against the reference answer as correct,
wrong, or no answer. Questions are written from the reference sources before any arm runs, so
neither arm's choices shape them. The learning conversation itself isn't drawn in either arm.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bench import learn
from tapestry import sources
from bench.extract import RESULTS
from bench.scenario import DEFAULT_CONTEXT, HARNESS_NOTE, ROOT, _claude
from tapestry import consolidate, embed
from tapestry.hosts import claude_code as cc
from tapestry.mind import Mind

DATA = ROOT / "bench" / "study"
OUT = Path(os.environ.get("STUDY_OUT", Path.home() / ".cache" / "tapestry-study"))
OWNER = "bench"
ARMS = ("read", "note")
BASE_TOOLS = ["tapestry_recall", "tapestry_note", "tapestry_why", "tapestry_topics"]
ASK_TOOLS = ["tapestry_recall", "tapestry_why", "tapestry_topics"]

SUBJECTS = {
    "hurricanes": {
        "topic": "hurricanes-2026",
        "about": "The 2026 Atlantic hurricane season: its storms, their tracks, strengths, impacts and records.",
        "ask": "the 2026 Atlantic hurricane season",
        "refs": ["https://en.wikipedia.org/wiki/2026_Atlantic_hurricane_season"],
        "today": "2026-11-25"},
    "gie-clocks": {
        "topic": "gie-clocks",
        "about": "The paper 'Testing Gravitationally-Induced-Entanglement with Massive Particles and "
                 "Quantum Clocks' (arXiv:2611.08313): its proposal, setup, results and claims.",
        "ask": "the paper \"Testing Gravitationally-Induced-Entanglement with Massive Particles and Quantum "
               "Clocks\" (arXiv:2611.08313, November 2026): what it proposes, how, and what it finds",
        "refs": ["https://arxiv.org/abs/2611.08313"],
        "today": "2026-11-25"},
}

LEARN_MESSAGE = ("I'd like you to learn about {ask}, so that in later conversations you can answer my "
                 "questions about it without looking anything up. Research it on the web now and keep "
                 "what you learn in your long-term memory. Tell me briefly what you covered when you're done.")
SKILLS = {
    "read": ("To learn a subject from sources: search the web, choose the sources most worth learning "
             "from, and give each to tapestry_read, which reads it into your memory after this "
             "conversation. Look at a source first only when you can't tell from its address and "
             "search result whether it's worth reading. Note with tapestry_note only what you "
             "conclude yourself across sources."),
    "note": ("To learn a subject from sources: search the web, read the sources most worth learning "
             "from, and save what they say that is worth knowing later with tapestry_note, source "
             "'web', one standalone fact per note that names the source it came from. Your memory "
             "keeps only what you note."),
}
BUDGET_USD = 2.00

QUESTION_SYSTEM = (
    "You write a test of whether someone learned a subject from its sources. From the source text "
    "given, write questions a person who studied this subject should be able to answer later, each "
    "with a short reference answer the text states or that follows directly from it. Cover the "
    "whole text, not just its opening. Mix specific details (names, numbers, dates), how things "
    "relate or why, and a few that need two separate parts of the text put together. Each question "
    "names its subject in full, since it is asked with no other context. No question the text "
    "doesn't answer.")
QUESTION_SCHEMA = {"type": "object", "required": ["questions"], "properties": {"questions": {
    "type": "array", "items": {"type": "object", "required": ["question", "answer", "kind"], "properties": {
        "question": {"type": "string"}, "answer": {"type": "string"},
        "kind": {"type": "string", "enum": ["detail", "relation", "combine"]}}}}}}

GRADE_SYSTEM = ("Grade an assistant's reply to a question against the reference answer. correct: it gives "
                "the reference answer, in any wording, with nothing contradicting it. wrong: it gives a "
                "different or contradicting answer, or hedges between answers. none: it says it doesn't "
                "know or gives no answer.")
GRADE_SCHEMA = json.dumps({"type": "object", "required": ["grade"], "properties": {
    "grade": {"type": "string", "enum": ["correct", "wrong", "none"]}}})

ANSWER_NOTE = "Answer briefly. If you don't know, say so rather than guess."


def _env(home: Path) -> dict:
    return {"TAPESTRY_HOME": str(home), "TAPESTRY_OWNER": OWNER}


def _with_env(env: dict, fn):
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return fn()
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _reminder(text: str) -> str:
    return f"<system-reminder>\n{text}\n</system-reminder>"


def _run(system: str, message: str, model: str, *, cwd: str, tools: str, extra: list[str]) -> dict:
    """One turn of headless Claude Code, kept whole even when the spending limit ends it."""
    proc = subprocess.run(
        ["claude", "-p", "--setting-sources", "", "--tools", tools, "--strict-mcp-config",
         "--exclude-dynamic-system-prompt-sections", "--no-session-persistence", "--model", model,
         "--system-prompt", system, "--output-format", "stream-json", "--verbose", *extra],
        input=message, capture_output=True, text=True, cwd=cwd, timeout=3600)
    texts, calls, pending, result = [], [], {}, {}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for b in ev["message"]["content"]:
                if b.get("type") == "text" and b.get("text"):
                    texts.append(b["text"])
                elif b.get("type") == "tool_use":
                    pending[b.get("id")] = c = {"tool": b.get("name"), "input": b.get("input")}
                    calls.append(c)
        elif ev.get("type") == "user":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    c = b.get("content")
                    pending[b["tool_use_id"]]["result"] = (c if isinstance(c, str) else
                                                           "".join(x.get("text", "") for x in c or []))[:2000]
        elif ev.get("type") == "result":
            result = ev
    if not result:
        raise RuntimeError(f"claude gave no result (exit {proc.returncode}): {proc.stderr[-400:]}")
    return {"reply": texts[-1] if texts else "", "calls": calls, "cost_usd": result.get("total_cost_usd") or 0.0,
            "stopped": result.get("subtype"), "turns": result.get("num_turns"),
            "seconds": (result.get("duration_ms") or 0) / 1000}


# -- questions -------------------------------------------------------------------------------

def cmd_questions(subject: str, n: int, model: str) -> int:
    s = SUBJECTS[subject]
    text = "\n\n".join(f"## {p['title']}\n" + "\n\n".join(f"### {h}\n{t}" if h else t for h, t in p["sections"])
                       for p in map(sources.read, s["refs"]))
    out = json.loads(_claude(["--output-format", "json", "--json-schema", json.dumps(QUESTION_SCHEMA)],
                             f"Write {n} questions.\n\nThe subject: {s['about']}\n\nThe source text:\n\n{text}",
                             model, QUESTION_SYSTEM))
    qs = (out.get("structured_output") or {}).get("questions")
    if not qs:
        print(f"no questions: {str(out.get('result'))[:300]}", file=sys.stderr)
        return 1
    DATA.mkdir(exist_ok=True)
    path = DATA / f"{subject}.json"
    path.write_text(json.dumps({"subject": subject, "refs": s["refs"], "model": model,
                                "written": dt.date.today().isoformat(),
                                "questions": [{"id": f"q{i}", **q} for i, q in enumerate(qs, 1)]}, indent=1))
    print(f"{len(qs)} questions in {path}")
    return 0


def _questions(subject: str) -> list[dict]:
    return json.loads((DATA / f"{subject}.json").read_text())["questions"]


# -- learning --------------------------------------------------------------------------------

def _home(subject: str, arm: str, run: int) -> Path:
    return OUT / subject / f"{arm}-{run}"


def cmd_learn(subject: str, arm: str, run: int, model: str, budget: float, drawer_effort: str,
              window: int = consolidate.WINDOW_CHARS) -> dict:
    s = SUBJECTS[subject]
    root = _home(subject, arm, run)
    if root.exists():
        shutil.rmtree(root)
    home, cwd = root / "home", root / s["topic"]
    (cwd / ".git").mkdir(parents=True)
    (home / "minds").mkdir(parents=True)
    env = _env(home)

    def start():
        mind = cc.open_mind(str(cwd))
        try:
            mind.topic(s["topic"], s["about"])
        finally:
            mind.close()
        return cc.on_session_start({"cwd": str(cwd)})["hookSpecificOutput"]["additionalContext"]
    started = _with_env(env, start)
    allowed = BASE_TOOLS + (["tapestry_read"] if arm == "read" else [])
    queue = root / "queue.jsonl"
    config = root / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"tapestry": {
        "command": sys.executable, "args": ["-m", "bench.study_mcp"], "cwd": str(ROOT),
        "env": {**env, "STUDY_TOOLS": ",".join(allowed), "STUDY_QUEUE": str(queue),
                "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"}}}}))
    today = dt.date.fromisoformat(s["today"]).strftime("%A, %B %-d, %Y")
    system = (f"{DEFAULT_CONTEXT}\n\n{HARNESS_NOTE}\n\nToday is {today}.\n\n{SKILLS[arm]}\n\n"
              + _reminder("SessionStart hook additional context: " + started))
    t0 = dt.datetime.now(dt.timezone.utc)
    got = _run(system, LEARN_MESSAGE.format(ask=s["ask"]), model, cwd=str(cwd), tools="WebSearch,WebFetch",
               extra=["--mcp-config", str(config), "--max-budget-usd", str(budget), "--allowedTools",
                      ",".join(["WebSearch", "WebFetch", *(f"mcp__tapestry__{t}" for t in allowed)])])
    print(f"[{subject} {arm}-{run}] session: ${got['cost_usd']:.2f}, {got['turns']} turns, "
          f"{len(got['calls'])} calls, stopped {got['stopped']}", flush=True)
    drawn = {"sources": 0, "facts": 0, "confirmed": 0, "cost_usd": 0.0, "seconds": 0.0, "failed": []}
    if arm == "read" and queue.exists():
        mind = _with_env(env, lambda: Mind(cc.mind_path(), encode=embed.Encoder(), model_tag=embed.MODEL_TAG))
        drawer = learn.PageDrawer("haiku", effort=drawer_effort)
        try:
            seen = set()
            for line in queue.read_text().splitlines():
                q = json.loads(line)
                try:
                    page = sources.read(q["url"])
                except sources.Unreadable as e:
                    drawn["failed"].append(str(e)); continue
                if page["url"] in seen:
                    continue
                seen.add(page["url"])
                for attempt in (1, 2):      # a drawing call can fail transiently; one retry, then record it
                    try:
                        did = learn.read_page(mind, page, drawer, topic=q["topic"] or s["topic"], about=s["about"],
                                              chars=window)
                        break
                    except RuntimeError as e:
                        did = None
                        err = f"{page['url']}: {e}"
                if did is None:
                    drawn["failed"].append(err); print(f"[{subject} {arm}-{run}] FAILED {err}", flush=True); continue
                drawn["sources"] += 1
                for k in ("facts", "confirmed", "cost_usd", "seconds"):
                    drawn[k] += did[k]
                print(f"[{subject} {arm}-{run}] read {page['title']}: {did['facts']} facts", flush=True)
        finally:
            mind.close()
    mind = _with_env(env, lambda: Mind(cc.mind_path(), encode=embed.Encoder(), model_tag=embed.MODEL_TAG))
    try:
        memories = mind.db.execute("SELECT count(*) FROM memories WHERE superseded_by IS NULL").fetchone()[0]
    finally:
        mind.close()
    row = {"subject": subject, "arm": arm, "run": run, "model": model, "budget_usd": budget, "window": window, "at": t0.isoformat(),
           "session_usd": got["cost_usd"], "session_s": got["seconds"], "stopped": got["stopped"],
           "turns": got["turns"], "searches": sum(c["tool"] == "WebSearch" for c in got["calls"]),
           "fetches": sum(c["tool"] == "WebFetch" for c in got["calls"]),
           "notes": sum(c["tool"].endswith("tapestry_note") for c in got["calls"]),
           "reads": sum(c["tool"].endswith("tapestry_read") for c in got["calls"]),
           "drawn": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in drawn.items()},
           "memories": memories, "reply": got["reply"]}
    (root / "learn.json").write_text(json.dumps({**row, "calls": got["calls"]}, indent=1))
    print(json.dumps({k: v for k, v in row.items() if k != "reply"}), flush=True)
    return row


# -- answering -------------------------------------------------------------------------------

def _prepare(q: dict, subject: str, mind_db: Path | None, tmp: Path) -> dict:
    """What one answering session is given. Run one at a time: the hooks read the home from the
    environment."""
    s = SUBJECTS[subject]
    today = dt.date.fromisoformat(s["today"]).strftime("%A, %B %-d, %Y")
    system = f"{DEFAULT_CONTEXT}\n\n{HARNESS_NOTE}\n\nToday is {today}."
    message = f"{q['question']}\n\n{ANSWER_NOTE}"
    if mind_db is None:          # closed book: the agent's own knowledge
        cwd = tmp / "closed"; cwd.mkdir(parents=True)
        return {"system": system, "message": message, "cwd": str(cwd), "tools": "", "extra": []}
    home, cwd = tmp / "home", tmp / s["topic"]
    (home / "minds").mkdir(parents=True)
    (cwd / ".git").mkdir(parents=True)
    shutil.copy(mind_db, home / "minds" / f"{OWNER}.db")
    env = _env(home)

    def hooks():
        start = cc.on_session_start({"cwd": str(cwd)})["hookSpecificOutput"]["additionalContext"]
        p = cc.on_prompt({"cwd": str(cwd), "prompt": q["question"]})
        return start, (p["hookSpecificOutput"]["additionalContext"] if p else "")
    start, context = _with_env(env, hooks)
    config = tmp / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"tapestry": {
        "command": str(ROOT / "bin" / "tapestry"), "args": ["mcp"],
        "env": {**env, "TAPESTRY_PYTHON": sys.executable}}}}))
    system += "\n\n" + _reminder("SessionStart hook additional context: " + start)
    if context:
        message += "\n\n" + _reminder("UserPromptSubmit hook additional context: " + context)
    return {"system": system, "message": message, "cwd": str(cwd), "tools": "Read", "shown": len(context),
            "extra": ["--mcp-config", str(config), "--allowedTools", ",".join(f"mcp__tapestry__{t}" for t in ASK_TOOLS)]}


def _grade(q: dict, reply: str, model: str) -> str:
    prompt = f"Question: {q['question']}\nReference answer: {q['answer']}\n\nAssistant's reply:\n{reply or '(empty)'}"
    try:
        out = json.loads(_claude(["--output-format", "json", "--json-schema", GRADE_SCHEMA], prompt, model, GRADE_SYSTEM))
        return out["structured_output"]["grade"]
    except Exception as e:
        return f"ERROR {type(e).__name__}: {e}"


def cmd_ask(subject: str, minds: list[str], model: str, judge: str, workers: int, closed: bool) -> list[dict]:
    qs = _questions(subject)
    arms = [(m, _home(subject, *m.split(":")[0:1], int(m.split(":")[1])) / "home" / "minds" / f"{OWNER}.db")
            for m in minds] + ([("closed", None)] if closed else [])
    jobs = [(name, db, q) for name, db in arms for q in qs]
    with tempfile.TemporaryDirectory(prefix="tapestry-study-") as root:
        ready = [_prepare(q, subject, db, Path(root) / str(i)) for i, (name, db, q) in enumerate(jobs)]

        def one(r: dict, q: dict) -> dict:
            try:
                got = _run(r["system"], r["message"], model, cwd=r["cwd"], tools=r["tools"], extra=r["extra"])
            except Exception as e:
                return {"error": f"ERROR {type(e).__name__}: {e}"}
            got["looked"] = sum(c["tool"].endswith("tapestry_recall") for c in got["calls"])
            return {**got, "shown": r.get("shown", 0), "grade": _grade(q, got["reply"], judge)}
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(one, ready, [q for _, _, q in jobs]))
    rows = []
    for (name, _, q), r in zip(jobs, results):
        rows.append({"subject": subject, "arm": name, "id": q["id"], "kind": q["kind"],
                     "grade": r.get("grade") or r.get("error"), "looked": r.get("looked"), "shown": r.get("shown"),
                     "cost_usd": r.get("cost_usd"), "reply": r.get("reply")})
        print(f"{subject} {name} {q['id']} [{q['kind']}]: {rows[-1]['grade']} looked={r.get('looked')}", flush=True)
    path = OUT / subject / "answers.jsonl"
    with open(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return rows


def _condition(subject: str, arm: str, m: dict) -> str:
    """Runs made the same way, whatever their number: the arm, how readings were drawn, and
    whether there was a learning session at all."""
    name = arm.split(":")[0]
    if name == "read":
        name += f" window={m.get('window') or consolidate.WINDOW_CHARS}"
        if "no session" in str(m.get("stopped") or ""):
            name += " (paper only)"
    return f"{subject} {name}"


def _groups(summary: dict) -> dict:
    import statistics as st
    out: dict[str, dict] = {}
    for key, m in summary.items():
        subject, arm = key.split(" ", 1)
        out.setdefault(_condition(subject, arm, m), []).append(m)
    sd = lambda xs: st.stdev(xs) if len(xs) > 1 else 0.0
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return {k: {"runs": len(ms), "questions": sum(m["questions"] for m in ms),
                "correct": round(mean([m["correct"] for m in ms]), 3), "correct_sd": round(sd([m["correct"] for m in ms]), 3),
                "wrong": round(mean([m["wrong"] for m in ms]), 3), "wrong_sd": round(sd([m["wrong"] for m in ms]), 3),
                "none": round(mean([m["none"] for m in ms]), 3),
                "learn_usd": round(mean([m["learn_usd"] or 0 for m in ms]), 3),
                "answer_usd": round(sum(m["answer_usd"] for m in ms), 3),
                "memories": mean([m["memories"] or 0 for m in ms])} for k, ms in out.items()}


def cmd_report(log: bool) -> int:
    summary = {}
    for subject in SUBJECTS:
        d = OUT / subject
        if not (d / "answers.jsonl").exists():
            continue
        rows = [json.loads(l) for l in (d / "answers.jsonl").read_text().splitlines()]
        learned = {f"{j['arm']}:{j['run']}": j for p in d.glob("*/learn.json") for j in [json.loads(p.read_text())]}
        for arm in sorted({r["arm"] for r in rows}):
            g = [r for r in rows if r["arm"] == arm]
            n = len(g)
            share = lambda k: round(sum(r["grade"] == k for r in g) / n, 3)
            j = learned.get(arm, {})
            summary[f"{subject} {arm}"] = {
                "questions": n, "correct": share("correct"), "wrong": share("wrong"), "none": share("none"),
                "errors": sum(r["grade"] not in ("correct", "wrong", "none") for r in g),
                "by_kind": {k: round(sum(r["grade"] == "correct" for r in g if r["kind"] == k) /
                                     max(1, sum(r["kind"] == k for r in g)), 2) for k in ("detail", "relation", "combine")},
                "looked": round(sum((r["looked"] or 0) > 0 for r in g) / n, 2),
                "memories": j.get("memories"), "learn_usd": round(j.get("session_usd", 0) +
                                                                  (j.get("drawn") or {}).get("cost_usd", 0), 3) if j else None,
                "session_usd": j.get("session_usd"), "draw_usd": (j.get("drawn") or {}).get("cost_usd"),
                "searches": j.get("searches"), "fetches": j.get("fetches"), "notes": j.get("notes"),
                "reads": j.get("reads"), "stopped": j.get("stopped"), "window": j.get("window"),
                "answer_usd": round(sum(r["cost_usd"] or 0 for r in g), 3)}
    print(f"{'':<24}{'correct':>8}{'wrong':>7}{'none':>6}{'mem':>6}{'learn$':>8}{'srch':>6}{'fetch':>6}{'note':>6}{'read':>6}")
    for k, m in summary.items():
        f = lambda v, w, fmt="": f"{'-' if v is None else format(v, fmt):>{w}}"
        print(f"{k:<24}{f(m['correct'], 8, '.2f')}{f(m['wrong'], 7, '.2f')}{f(m['none'], 6, '.2f')}"
              f"{f(m['memories'], 6)}{f(m['learn_usd'], 8, '.2f')}{f(m['searches'], 6)}{f(m['fetches'], 6)}"
              f"{f(m['notes'], 6)}{f(m['reads'], 6)}")
        print(f"{'':<24}  by kind {m['by_kind']}  looked {m['looked']}  errors {m['errors']}  stopped {m['stopped']}")
    groups = _groups(summary)
    print(f"\n{'by condition (mean ± sd over runs)':<36}{'runs':>5}{'correct':>14}{'wrong':>14}{'learn$':>8}{'mem':>6}")
    for k, m in groups.items():
        print(f"{k:<36}{m['runs']:>5}{m['correct']:>8.2f} ±{m['correct_sd']:.2f}{m['wrong']:>8.2f} ±{m['wrong_sd']:.2f}"
              f"{m['learn_usd']:>8.2f}{m['memories']:>6.0f}")
    if log:
        with open(RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "study", "metrics": {"conditions": groups, "runs": summary}}) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.study", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("questions"); q.add_argument("subject", choices=SUBJECTS)
    q.add_argument("-n", type=int, default=30); q.add_argument("--model", default="opus")
    lr = sub.add_parser("learn"); lr.add_argument("subject", choices=SUBJECTS); lr.add_argument("arm", choices=ARMS)
    lr.add_argument("--run", type=int, default=1); lr.add_argument("--model", default="sonnet")
    lr.add_argument("--budget", type=float, default=BUDGET_USD); lr.add_argument("--drawer-effort", default="low")
    lr.add_argument("--window", type=int, default=consolidate.WINDOW_CHARS, help="characters one drawing call reads")
    a = sub.add_parser("ask"); a.add_argument("subject", choices=SUBJECTS)
    a.add_argument("minds", nargs="*", help="ARM:RUN, e.g. read:1 note:1")
    a.add_argument("--no-closed", action="store_true"); a.add_argument("--model", default="sonnet")
    a.add_argument("--judge-model", default="sonnet"); a.add_argument("--workers", type=int, default=6)
    r = sub.add_parser("report"); r.add_argument("--log", action="store_true")
    x = ap.parse_args(argv)
    if x.cmd == "questions":
        return cmd_questions(x.subject, x.n, x.model)
    if x.cmd == "learn":
        cmd_learn(x.subject, x.arm, x.run, x.model, x.budget, x.drawer_effort, x.window)
        return 0
    if x.cmd == "ask":
        rows = cmd_ask(x.subject, x.minds, x.model, x.judge_model, x.workers, not x.no_closed)
        return 1 if any(r["grade"] not in ("correct", "wrong", "none") for r in rows) else 0
    return cmd_report(x.log)


if __name__ == "__main__":
    sys.exit(main())
