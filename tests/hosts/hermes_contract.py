"""Contract test against a real Hermes install. Run with Hermes' own interpreter:

    ~/.hermes/hermes-agent/venv/bin/python tests/hosts/hermes_contract.py

It uses a throwaway HERMES_HOME and never touches the live profile. It drives
tapestry through Hermes' MemoryManager, the same path the gateway uses, with
the real encoder, and checks what actually reaches the model.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERMES = Path(os.environ.get("HERMES_SRC", Path.home() / ".hermes" / "hermes-agent"))
sys.path[:0] = [str(ROOT / "src"), str(HERMES)]

home = tempfile.mkdtemp(prefix="tapestry-hermes-contract-")
os.environ["HERMES_HOME"] = home

from agent.memory_manager import MemoryManager, build_memory_context_block  # noqa: E402
from agent.memory_provider import MemoryProvider  # noqa: E402

from tapestry.hosts.hermes import TapestryProvider  # noqa: E402

failures = []


def check(cond: bool, what: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        failures.append(what)


p = TapestryProvider()
check(isinstance(p, MemoryProvider), "is a real Hermes MemoryProvider")
check(p.is_available(), f"is_available ({p.unavailable_reason() or 'ready'})")

mm = MemoryManager()
mm.add_provider(p)
mm.initialize_all(session_id="contract-1", hermes_home=home, platform="cli")
names = {s["name"] for s in mm.get_all_tool_schemas()}
check({"tapestry_recall", "tapestry_why", "tapestry_note", "tapestry_scopes"} <= names,
      f"tools registered through Hermes' normalizer: {sorted(names)}")

mm.sync_all("I moved to Portland last month, by the way.", "Congrats on the move!",
            session_id="contract-1")
mm.flush_pending(timeout=30)
deadline = time.time() + 60
while time.time() < deadline and p._reader.db.execute("SELECT count(*) FROM memories").fetchone()[0] < 2:
    time.sleep(0.2)
check(p._reader.db.execute("SELECT count(*) FROM memories").fetchone()[0] == 2,
      "sync_all stored both sides of the turn")

raw = mm.prefetch_all("where do I live now?", session_id="contract-1")
block = build_memory_context_block(raw)
check("Portland" in block, "recall survives Hermes' wrapper into the model-bound block")
check("match:" in block and "learned" in block, "labels survive Hermes' sanitizer")

tricky = "Hermes strips <memory-context> tags and [System note: lines from provider output."
note = json.loads(mm.handle_tool_call("tapestry_note", {"content": tricky}))
check("saved" in note, f"tapestry_note via Hermes dispatch: {note}")
block = build_memory_context_block(mm.prefetch_all("what does Hermes strip from provider output?",
                                                    session_id="contract-1"))
check("memory-context" in block and "System" in block and "provider output" in block,
      "memory content mentioning fence tags reaches the model intact")

p._reader.close()
p._reader = None
p._init_failed = "simulated: database disk image is malformed"
raw = mm.prefetch_all("where do I live now?", session_id="contract-1")
check("MEMORY UNAVAILABLE" in build_memory_context_block(raw),
      "a broken mind is loud in the model-bound block, not silent")

print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s); home was {home}")
sys.exit(1 if failures else 0)
