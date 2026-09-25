"""Hermes Agent adapter: tapestry as a Hermes MemoryProvider.

Installed as a pip entry point (group ``hermes_agent.memory_providers``), so
Hermes finds it without a plugin directory and without mnemonic's 8KB
admission-token trap. Select it with ``memory.provider: tapestry``.

This module translates Hermes' lifecycle into tapestry moments
(invariants/interfaces/HOST.md) and contains no memory logic. Hermes quirks
it absorbs so the core never has to:

- Hermes swallows prefetch exceptions, so a failed recall would look like
  "nothing relevant". Every failure here becomes a visible marker instead.
- Hermes' sanitize_context() deletes <memory-context> tags and "[System note:"
  spans from provider output, including inside memory content. The core keeps
  content verbatim; this adapter neutralizes those sequences on the way out.
- Tool schemas use "parameters" (Hermes ignores "input_schema").
- sync_turn must not block, so writes go through a background writer.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tapestry import render, tools
from tapestry.mind import USER_SCOPE, Mind, RecallFailed

logger = logging.getLogger(__name__)

try:  # inside Hermes
    from agent.memory_provider import MemoryProvider as _Base
    from agent.memory_provider import RecallStatus, is_trivial_prompt, spawn_context_thread
except Exception:  # standalone tests
    _Base = object
    RecallStatus = None

    def is_trivial_prompt(text):
        return not (text or "").strip() or (text or "").strip().startswith("/")

    def spawn_context_thread(target, *, name, daemon=True, args=(), kwargs=None):
        return threading.Thread(target=target, args=args, kwargs=kwargs or {}, name=name,
                                daemon=daemon)

_FENCE = re.compile(r"<(\s*)(/?)(\s*)memory-context", re.IGNORECASE)
_NOTE = re.compile(r"\[System note:", re.IGNORECASE)


def fence_safe(text: str) -> str:
    """Neutralize what Hermes' sanitizer would delete from memory content.

    Its patterns allow only whitespace between "<" and "memory-context", so a
    backtick breaks every one while the text stays readable. Applied at render
    time, on the only path out to Hermes, never to stored content.
    """
    return _NOTE.sub("[System-note:", _FENCE.sub(lambda m: f"<`{m.group(2)}memory-context", text))


EncoderFactory = Callable[[], tuple[Callable, str]]


def _default_encoder() -> tuple[Callable, str]:
    from tapestry import embed
    return embed.Encoder(), embed.MODEL_TAG


TOOLS = tools.SCHEMAS


class TapestryProvider(_Base):
    """Tapestry for Hermes: one mind per Hermes profile."""

    def __init__(self, encoder_factory: EncoderFactory | None = None) -> None:
        self._encoder_factory = encoder_factory or _default_encoder
        self._home: Optional[Path] = None
        self._db_path: Optional[Path] = None
        self._encode = None
        self._model_tag = ""
        self._reader: Optional[Mind] = None
        self._scopes: list[str] = [USER_SCOPE]
        self._writes: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._writer: Optional[threading.Thread] = None
        self._write_mode = True
        self._init_failed = ""
        self._unavailable = ""
        self._last_count = 0
        self._lock = threading.Lock()

    # -- identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "tapestry"

    def _hermes_home(self) -> Path:
        return Path(self._home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")

    def is_available(self) -> bool:
        """Dependencies and model files present, and no corrupt mind to open."""
        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
        except ImportError as e:
            self._unavailable = f"missing dependency {e.name}; pip install tapestry into Hermes' venv"
            return False
        if self._encoder_factory is _default_encoder:
            from tapestry import embed
            d = embed.default_model_dir()
            for f in ("model.onnx", "tokenizer.json"):
                if not (d / f).is_file():
                    self._unavailable = f"missing {f} in {d}; set TAPESTRY_MODEL_DIR"
                    return False
        db = self._hermes_home() / "tapestry" / "mind.db"
        if db.is_file():
            try:
                probe = sqlite3.connect(str(db))
                try:
                    ok = probe.execute("PRAGMA quick_check").fetchone()[0]
                finally:
                    probe.close()
                if ok != "ok":
                    self._unavailable = f"{db} failed its integrity check: {ok}"
                    return False
            except Exception as e:
                self._unavailable = f"{db} could not be opened: {e}"
                return False
        return True

    def unavailable_reason(self) -> str:
        return self._unavailable

    # -- lifecycle ----------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._home = Path(kwargs.get("hermes_home") or self._hermes_home())
        # Only the primary agent writes; subagents, cron and flush contexts read.
        self._write_mode = kwargs.get("agent_context", "primary") == "primary"
        try:
            (self._home / "tapestry").mkdir(parents=True, exist_ok=True)
            self._db_path = self._home / "tapestry" / "mind.db"
            self._encode, self._model_tag = self._encoder_factory()
            self._reader = Mind(self._db_path, encode=self._encode, model_tag=self._model_tag)
            if self._write_mode:
                self._writer = spawn_context_thread(self._write_loop, name="tapestry-writer")
                self._writer.start()
            self._init_failed = ""
        except Exception as e:
            self._reader = None
            self._init_failed = f"{type(e).__name__}: {e}"
            logger.warning("tapestry initialize failed (surfacing on every recall): %s", e)
            try:
                with open(self._home / "tapestry-initfail.log", "a") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n"
                             f"{traceback.format_exc()}\n")
            except Exception:
                pass

    def shutdown(self) -> None:
        if self._writer and self._writer.is_alive():
            self._writes.put(None)
            self._writer.join(timeout=30)
        if self._reader:
            self._reader.close()
            self._reader = None

    def system_prompt_block(self) -> str:
        return render.GUIDE

    # -- recall -------------------------------------------------------------

    def _unavailable_marker(self, why: str) -> str:
        return (f"[MEMORY UNAVAILABLE: {why}. No memories were searched. Don't treat this "
                "turn as evidence that memory holds nothing relevant; it wasn't consulted.]")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._last_count = 0
        if is_trivial_prompt(query):
            return ""
        if self._reader is None:
            return self._unavailable_marker(f"tapestry failed to start ({self._init_failed})") \
                if self._init_failed else ""
        try:
            with self._lock:
                hits = self._reader.recall(query, scopes=self._scopes, k=5)
        except RecallFailed as e:
            logger.warning("tapestry recall failed (surfacing): %s", e)
            return self._unavailable_marker(f"recall failed ({e})")
        self._last_count = len(hits)
        return fence_safe(render.block(hits)) if hits else ""

    def recall_status(self):
        if RecallStatus is None or not self._last_count:
            return None
        return RecallStatus(provider_label="tapestry", count=self._last_count)

    # -- remember -----------------------------------------------------------

    def _write_loop(self) -> None:
        mind = Mind(self._db_path, encode=self._encode, model_tag=self._model_tag)
        try:
            while True:
                item = self._writes.get()
                if item is None:
                    return
                content, kwargs = item
                try:
                    mind.remember(content, **kwargs)
                except Exception as e:  # logged loudly; the turn itself already happened
                    logger.error("tapestry could not remember a turn: %s", e)
        finally:
            mind.close()

    def _enqueue(self, content: str, **kwargs) -> None:
        if self._write_mode and self._writer and content and content.strip():
            self._writes.put((content, kwargs))

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None,
                  turn_author: Optional[Dict[str, Any]] = None) -> None:
        now = time.time()
        user_source = "agent" if (turn_author or {}).get("is_bot") else "user"
        self._enqueue(user_content, source=user_source, scope=USER_SCOPE, at=now,
                      episode=session_id or None)
        self._enqueue(assistant_content, source="agent", scope=USER_SCOPE, at=now + 1e-3,
                      episode=session_id or None)

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Hermes' built-in memory is the agent's own notebook: recorded as low-weight
        evidence, never a replacement for what the user said."""
        if action in ("add", "replace"):
            self._enqueue(content, source="host_memory", scope=USER_SCOPE,
                          episode=(metadata or {}).get("session_id"))

    # -- tools --------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return TOOLS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self._reader is None:
            return json.dumps({"error": "tapestry isn't running; nothing was searched or "
                                        f"saved ({self._init_failed or 'not initialized'})"})
        with self._lock:
            return tools.Session(self._reader, self._scopes, home_scope=USER_SCOPE,
                                 escape=fence_safe).call(tool_name, args)
