"""Smoke test: the Claude Agent SDK loading tapestry as a local plugin.

    TAPESTRY_PYTHON=.venv/bin/python .venv/bin/python tests/hosts/agent_sdk_smoke.py

Seeds a throwaway mind with one project memory, asks a question only that
memory answers, and checks the answer uses it. Makes one small model call.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
home = tempfile.mkdtemp(prefix="tapestry-sdk-")
os.environ.update(TAPESTRY_HOME=home, TAPESTRY_OWNER="sdk")
os.environ.setdefault("TAPESTRY_PYTHON", str(ROOT / ".venv" / "bin" / "python"))
cwd = Path(home) / "proj" / "atlas"
(cwd / ".git").mkdir(parents=True)

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query  # noqa: E402

from tapestry.hosts import claude_code as cc  # noqa: E402

mind = cc.open_mind(str(cwd))
mind.remember("The staging database for atlas listens on port 5433.", source="user", scope="atlas")
mind.close()


async def main() -> str:
    opts = ClaudeAgentOptions(cwd=str(cwd), plugins=[{"type": "local", "path": str(ROOT)}],
                              setting_sources=[], model="sonnet", max_turns=3)
    text = []
    async for msg in query(prompt="What port does the atlas staging database use? One sentence.",
                           options=opts):
        if isinstance(msg, AssistantMessage):
            text += [b.text for b in msg.content if isinstance(b, TextBlock)]
    return " ".join(text)


answer = asyncio.run(main())
print(answer)
ok = "5433" in answer
print("PASS" if ok else "FAIL: the answer didn't use the recalled memory")
sys.exit(0 if ok else 1)
