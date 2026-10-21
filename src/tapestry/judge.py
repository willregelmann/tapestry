"""A model's judgment about memories, kept apart from the memories it judges.

docs/primitives/MEMORY.md: whether something new confirms, contradicts or replaces a memory
takes judgment, and the model only proposes. A judge here answers one question about a pair,
in coarse categories, and never sees more of a mind than the pair needs
(docs/primitives/MIND.md). What to do with the answer is the mind's business.

The only judge so far is Jev, through the Vercel AI Gateway, restricted to TypeSafe on every
request. The owner has agreed to memories going to it. No zero-data-retention flag is sent,
because the gateway refuses it on the plan the key is on.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
MODEL = "typesafe-ai/jev-1.13.0"   # pinned, never the moving alias
PROVIDER = "typesafe-ai"

# Jev's cut-offs. 0.7 was read off one set of 30 statements (12 of 31 old values found, 1 false
# conflict in 256) and held on another (15 found, 0 false in 248); at 0.9 it found 2 of 31 on both
# (bench/judge_compare.py). A wrong conflict lowers belief in two memories, so it stays above 0.5,
# where the first wording fired on unrelated ones.
CONFLICT_P = 0.7   # at or above: the model says they conflict
MAYBE_P = 0.5

# Measured on LongMemEval updates, whose labels count a changed value as a conflict.
PROMPT = "conflicts_v1"
NOUL = {
    "question": "Is this message incompatible with `memory`?",
    "criteria": {"true": "Both say something about the very same thing and cannot both be right: a "
                         "different value, an opposite claim, or one says the other is wrong",
                 "false": "They are about different things, or could both be true, or one only adds "
                          "to, answers or follows up the other"}}


def category(p: float) -> str:
    return "conflict" if p >= CONFLICT_P else "maybe" if p >= MAYBE_P else "no conflict"


class Judge(Protocol):
    prompt: str    # what it was asked
    model: str     # who answers

    def conflicts(self, statement: str, others: list[tuple[int, str]]) -> dict[int, str]:
        """For each (id, text) in `others`: 'conflict', 'maybe' or 'no conflict' against `statement`."""


class Retryable(RuntimeError):
    """Transient: safe to try the same memory again later."""


class JevJudge:
    prompt = PROMPT
    model = MODEL

    def __init__(self, key_file: Path | str | None = None, *, key: str | None = None,
                 send: Callable[[dict], dict] | None = None, timeout: float = 30.0) -> None:
        self._key = (key if key is not None else Path(key_file).read_text()).strip()
        if not self._key:
            raise ValueError(f"{key_file or 'the key'} is empty")
        self._timeout = timeout
        self._send = send or self._http

    def _http(self, body: dict) -> dict:
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = f"Jev HTTP {e.code}"
            if e.code in (429, 529, 500, 502, 503, 504):
                raise Retryable(msg) from None
            raise RuntimeError(msg) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise Retryable(f"Jev unreachable: {type(e).__name__}") from None

    @staticmethod
    def request(statement: str, others: list[tuple[int, str]]) -> dict:
        return {"model": MODEL, "state": {"message": statement},
                "questions": {f"c:{i}": {"type": "noul", "criteria": NOUL["criteria"],
                                         "instructions": {"memory": text, "question": NOUL["question"]}}
                              for i, (_, text) in enumerate(others)},
                "providerOptions": {"gateway": {"only": [PROVIDER]}}}

    def conflicts(self, statement: str, others: list[tuple[int, str]]) -> dict[int, str]:
        if not others:
            return {}
        resp = self._send(self.request(statement, others))
        routing = ((resp.get("provider_metadata") or {}).get("gateway") or {}).get("routing") or {}
        if routing.get("finalProvider") != PROVIDER:
            raise RuntimeError(f"answered by {routing.get('finalProvider')!r}, not TypeSafe")
        if len(routing.get("modelAttempts") or [None]) != 1:
            raise RuntimeError("more than one model attempt: a fallback may have answered")
        answers, out = resp.get("answers") or {}, {}
        for i, (mid, _) in enumerate(others):
            a = answers.get(f"c:{i}")
            if a is None or a.get("type") != "noul" or not 0.0 <= float(a["noul"]) <= 1.0:
                raise RuntimeError(f"Jev gave no usable answer for #{mid}")
            out[mid] = category(float(a["noul"]))
        return out


_VERDICTS = ("conflict", "maybe", "no conflict")
_SYSTEM = (
    "You compare one new message with numbered memories and decide, for each memory, whether the "
    "message is incompatible with it. conflict: both say something about the very same thing and "
    "cannot both be right (a different value, an opposite claim, or one says the other is wrong). "
    "no conflict: they are about different things, or could both be true, or one only adds to, "
    "answers or follows up the other. maybe: you cannot tell. Give one verdict for every memory.")
_SCHEMA = {"type": "object", "required": ["answers"], "properties": {"answers": {
    "type": "array", "items": {"type": "object", "required": ["id", "verdict"], "properties": {
        "id": {"type": "integer"}, "verdict": {"type": "string", "enum": list(_VERDICTS)}}}}}}


class ClaudeJudge:
    """The host's own model as the judge: `claude -p`, on the Claude Code login the user already has.

    Run with every customization off (--safe-mode: no hooks, plugins, MCP servers or CLAUDE.md), so
    the judging session can't trigger Tapestry's own hooks or read anything but the pair it's given.
    Nothing is sent that Claude Code wouldn't send anyway. `last` holds what the latest call cost
    and how long it took, for measuring.
    """
    prompt = "conflicts_claude_v1"

    def __init__(self, model: str = "haiku", *, effort: str | None = "low", timeout: float = 120.0,
                 run: Callable[[list[str], str], str] | None = None) -> None:
        self.alias, self.effort, self._timeout = model, effort, timeout
        self.model = f"claude:{model}"          # replaced by the model that answered, once one has
        self._run = run or self._subprocess
        self.last: dict = {}

    def _subprocess(self, argv: list[str], message: str) -> str:
        try:
            done = subprocess.run(argv, input=message, capture_output=True, text=True, timeout=self._timeout,
                                  cwd=tempfile.gettempdir())
        except (subprocess.TimeoutExpired, OSError) as e:
            raise Retryable(f"claude unavailable: {type(e).__name__}") from None
        if done.returncode != 0:
            raise RuntimeError(f"claude exited {done.returncode}: {done.stderr.strip()[:200]}")
        return done.stdout

    def conflicts(self, statement: str, others: list[tuple[int, str]]) -> dict[int, str]:
        if not others:
            return {}
        argv = ["claude", "-p", "--safe-mode", "--model", self.alias, "--tools", "", "--no-session-persistence",
                "--output-format", "json", "--system-prompt", _SYSTEM, "--json-schema", json.dumps(_SCHEMA)]
        if self.effort:
            argv += ["--effort", self.effort]
        message = ("New message:\n" + statement + "\n\n" + "\n\n".join(
            f"Memory {i}:\n{text}" for i, (_, text) in enumerate(others, 1)))
        t0 = time.monotonic()
        out = json.loads(self._run(argv, message))
        wall = time.monotonic() - t0
        if out.get("is_error"):
            raise RuntimeError(f"claude reported an error: {str(out.get('result'))[:200]}")
        used = list((out.get("modelUsage") or {}))
        self.model = f"claude:{used[0]}" if used else self.model
        self.last = {"cost_usd": out.get("total_cost_usd"), "api_s": (out.get("duration_api_ms") or 0) / 1000,
                     "wall_s": wall, "model": self.model}
        got = {a["id"]: a["verdict"] for a in (out.get("structured_output") or {}).get("answers", [])
               if isinstance(a, dict)}
        result = {}
        for i, (mid, _) in enumerate(others, 1):
            if got.get(i) not in _VERDICTS:
                raise RuntimeError(f"claude gave no usable verdict for #{mid}")
            result[mid] = got[i]
        return result


CHOICES = ("jev", "claude", "none")

DISCLOSURE = {
    "jev": ("Memories that sit close together are sent to TypeSafe's Jev model, through the Vercel AI "
            "Gateway with your key, to check whether they conflict. No zero-data-retention flag is "
            "sent, so protection rests on Vercel's and TypeSafe's published terms. Nothing else is sent."),
    "claude": ("Memories that sit close together are sent to Claude (Haiku), through the Claude Code "
               "login you already use, to check whether they conflict. It is the same service your "
               "conversations go to. Nothing else is sent."),
    "none": "Memories are never sent anywhere. Conflicts between them are not looked for in the background.",
}


class JudgeUnavailable(RuntimeError):
    """The owner chose a judge this machine can't run. Reported, never quietly swapped for another."""


def _config(home: Path) -> Path:
    return home / "config.json"


# What the plugin's settings dialog hands to every hook (userConfig keys `judge` and `jev_key`).
PLUGIN_JUDGE, PLUGIN_JEV_KEY = "CLAUDE_PLUGIN_OPTION_JUDGE", "CLAUDE_PLUGIN_OPTION_JEV_KEY"


def choice(home: Path) -> str | None:
    """Which judge the owner chose, or None when they haven't. The plugin's setting, when set, is
    the choice; `tapestry judge-with` is for everyone else."""
    chosen = os.environ.get(PLUGIN_JUDGE, "").strip()
    if chosen:
        if chosen not in CHOICES:
            raise JudgeUnavailable(f"the plugin setting names an unknown judge {chosen!r}; expected one of {CHOICES}")
        return chosen
    try:
        text = _config(home).read_text()
    except FileNotFoundError:
        return None
    try:
        which = json.loads(text).get("judge")
    except (ValueError, AttributeError):
        raise JudgeUnavailable(f"{_config(home)} can't be read") from None
    if which is not None and which not in CHOICES:
        raise JudgeUnavailable(f"{_config(home)} names an unknown judge {which!r}; expected one of {CHOICES}")
    return which


def choose(home: Path, which: str) -> str:
    """Record the owner's choice of judge. Returns where memories go, to be shown to them."""
    if which not in CHOICES:
        raise ValueError(f"unknown judge {which!r}; expected one of {CHOICES}")
    home.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(_config(home).read_text())
    except (FileNotFoundError, ValueError):
        config = {}
    tmp = _config(home).with_suffix(".tmp")
    tmp.write_text(json.dumps({**config, "judge": which}, indent=1) + "\n")
    tmp.replace(_config(home))
    return DISCLOSURE[which]


def configured(home: Path) -> Judge | None:
    """The judge this machine is set up for, or None. Raises JudgeUnavailable when the owner chose one
    that can't run.

    Memories leave the machine only by the owner's act. Having put a Jev key in place is such an act,
    so with no choice made Jev is used if its key is there. Claude is used only when chosen. The key
    is a file, never an argument.
    """
    which = choice(home)
    if which == "none":
        return None
    key = Path(os.environ.get("TAPESTRY_JEV_KEY_FILE") or home / "jev.key")
    plugin_key = os.environ.get(PLUGIN_JEV_KEY, "").strip()
    if which == "claude":
        if shutil.which("claude") is None:
            raise JudgeUnavailable("the judge is set to claude, but the claude command isn't on the path")
        return ClaudeJudge()
    if plugin_key:
        return JevJudge(key=plugin_key)
    if key.is_file():
        return JevJudge(key)
    if which == "jev":
        raise JudgeUnavailable(f"the judge is set to jev, but there is no key: set it in the plugin's "
                               f"settings or put it at {key}")
    return None
