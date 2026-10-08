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

from tapestry import guide, render, tools
from tapestry.mind import Mind, RecallFailed

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

SESSION_PREFIX = "session."
_NS_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def session_namespace(session_id: str) -> str:
    """The namespace a session's own turns are filed in.

    A turn is evidence of what was said, not a claim for every context, so it
    is filed where only its own session searches automatically. Other sessions
    still reach it by naming the namespace in tapestry_recall.
    """
    slug = _NS_UNSAFE.sub("-", (session_id or "").lower()).strip("-._") or "unknown"
    return SESSION_PREFIX + slug


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

# Hermes spills any memory-prefetch result over hooks.output_spill.max_chars (default 10,000) to
# a file and injects only its first and last 500 characters, so a 10.1k recall lands as ~1k: one
# memory and a pointer nobody opens. On ha-pi that was 63% of recall turns (2026-10-08). Rendering
# to a budget under the cap keeps whole memories in rank order instead. The margin covers
# fence_safe's added characters and the "didn't fit" line.
PREFETCH_BUDGET = 9_000


_ENCODER_LOCK = threading.Lock()
_ENCODER: Optional[tuple[Callable, str]] = None


def _default_encoder() -> tuple[Callable, str]:
    """One encoder per process, shared by every provider.

    Hermes builds a provider per agent, and a gateway builds agents per session,
    cron run and resume. Each ONNX session costs ~30MB resident on aarch64; one
    per provider grew a Raspberry Pi gateway past its RAM in a day. Encoding is
    stateless, so sharing is safe.
    """
    global _ENCODER
    with _ENCODER_LOCK:
        if _ENCODER is None:
            from tapestry import embed
            _ENCODER = (embed.Encoder(), embed.MODEL_TAG)
        return _ENCODER


class _Store:
    """Everything that should exist once per mind per process: the read
    connection, the write queue and its single writer thread.

    Hermes builds a provider per agent, and the gateway builds agents per
    session, per cron run and again on resume after cache eviction, which drops
    the old agent without calling shutdown(). When each provider owned its own
    connections, writer thread and encoder, those piled up for the life of the
    process: on ha-pi the gateway went from ~400MB to past 1.4GB RSS plus full
    swap in 14 hours and the Pi hung. One store per mind makes the cost of a
    provider a few small objects, however many Hermes creates or forgets.

    A single writer also means one connection ever writes, so concurrent
    providers can't contend for SQLite's write lock.
    """

    def __init__(self, db_path: Path, encode: Callable, model_tag: str) -> None:
        self.reader = Mind(db_path, encode=encode, model_tag=model_tag)
        self.lock = threading.RLock()  # serializes use of the shared read connection
        self.writes: "queue.Queue[Any]" = queue.Queue()
        self._db_path, self._encode, self._model_tag = db_path, encode, model_tag
        self._writer: Optional[threading.Thread] = None
        self._writer_lock = threading.Lock()

    def ensure_writer(self) -> None:
        with self._writer_lock:
            if self._writer is None or not self._writer.is_alive():
                self._writer = spawn_context_thread(self._write_loop, name="tapestry-writer")
                self._writer.start()

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until everything queued before this call has been written."""
        if self._writer is None or not self._writer.is_alive():
            return True
        done = threading.Event()
        self.writes.put(done)
        return done.wait(timeout)

    def _write_loop(self) -> None:
        mind = Mind(self._db_path, encode=self._encode, model_tag=self._model_tag)
        try:
            while True:
                item = self.writes.get()
                if isinstance(item, threading.Event):
                    item.set()
                    continue
                content, kwargs = item
                try:
                    mind.remember(content, **kwargs)
                except Exception as e:  # logged loudly; the turn itself already happened
                    logger.error("tapestry could not remember a turn: %s", e)
        finally:
            mind.close()


_STORES: Dict[tuple, _Store] = {}
_STORES_LOCK = threading.Lock()


def _store_for(db_path: Path, encode: Callable, model_tag: str) -> _Store:
    key = (str(Path(db_path).resolve()), model_tag)
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = _STORES[key] = _Store(db_path, encode, model_tag)
        return store


TOOLS = tools.SCHEMAS


class TapestryProvider(_Base):
    """Tapestry for Hermes: one mind per Hermes profile."""

    def __init__(self, encoder_factory: EncoderFactory | None = None) -> None:
        self._encoder_factory = encoder_factory or _default_encoder
        self._home: Optional[Path] = None
        self._db_path: Optional[Path] = None
        self._encode = None
        self._model_tag = ""
        self._store: Optional[_Store] = None
        self._open: list[str] = []  # namespaces open this session; default is implicit
        self._session_id = ""
        self._write_mode = True
        self._init_failed = ""
        self._unavailable = ""
        self._last_count = 0

    # The shared store's pieces, under the names the rest of this class uses.
    @property
    def _reader(self) -> Optional[Mind]:
        return self._store.reader if self._store else None

    @property
    def _lock(self):
        return self._store.lock if self._store else threading.RLock()

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
        self._session_id = session_id or ""
        self._home = Path(kwargs.get("hermes_home") or self._hermes_home())
        # Only the primary agent writes; subagents, cron and flush contexts read.
        self._write_mode = kwargs.get("agent_context", "primary") == "primary"
        try:
            (self._home / "tapestry").mkdir(parents=True, exist_ok=True)
            self._db_path = self._home / "tapestry" / "mind.db"
            self._encode, self._model_tag = self._encoder_factory()
            self._store = _store_for(self._db_path, self._encode, self._model_tag)
            if self._write_mode:
                self._store.ensure_writer()
            self._init_failed = ""
        except Exception as e:
            self._store = None
            self._init_failed = f"{type(e).__name__}: {e}"
            logger.warning("tapestry initialize failed (surfacing on every recall): %s", e)
            try:
                with open(self._home / "tapestry-initfail.log", "a") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n"
                             f"{traceback.format_exc()}\n")
            except Exception:
                pass

    def shutdown(self) -> None:
        """Make this provider's writes durable, then let go of the store.

        The store itself stays open: it is shared with every other provider on
        the same mind in this process, and is reclaimed when the process ends.
        """
        if self._store is not None:
            if self._write_mode and not self._store.flush(timeout=30):
                logger.warning("tapestry shutdown: queued writes did not finish within 30s")
            self._store = None

    def system_prompt_block(self) -> str:
        return guide.GUIDE + (self._directory() if self._reader else "")

    def _directory(self) -> str:
        try:
            with self._lock:
                own = session_namespace(self._session_id)
                listed = [n for n in self._reader.namespaces()
                          if not n["name"].startswith(SESSION_PREFIX) or n["name"] == own]
                hidden = sum(1 for n in self._reader.namespaces()
                             if n["name"].startswith(SESSION_PREFIX) and n["name"] != own)
                return render.directory(listed, self._searched(), hidden_sessions=hidden)
        except Exception:
            return ""

    # -- recall -------------------------------------------------------------

    def _unavailable_marker(self, why: str) -> str:
        return (f"[MEMORY UNAVAILABLE: {why}. No memories were searched. Don't treat this "
                "turn as evidence that memory holds nothing relevant; it wasn't consulted.]")

    def _searched(self, session_id: str = "") -> list[str]:
        """Open namespaces plus this session's own transcript."""
        own = session_namespace(session_id or self._session_id)
        return self._open + ([own] if own not in self._open else [])

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._last_count = 0
        if is_trivial_prompt(query):
            return ""
        if self._reader is None:
            return self._unavailable_marker(f"tapestry failed to start ({self._init_failed})") \
                if self._init_failed else ""
        try:
            with self._lock:
                hits = self._reader.recall(query, namespaces=self._searched(session_id), k=5)
        except RecallFailed as e:
            logger.warning("tapestry recall failed (surfacing): %s", e)
            return self._unavailable_marker(f"recall failed ({e})")
        self._last_count = len(hits)
        return fence_safe(render.budgeted(hits, PREFETCH_BUDGET)) if hits else ""

    def recall_status(self):
        if RecallStatus is None or not self._last_count:
            return None
        return RecallStatus(provider_label="tapestry", count=self._last_count)

    # -- remember -----------------------------------------------------------

    def _enqueue(self, content: str, **kwargs) -> None:
        if self._write_mode and self._store is not None and content and content.strip():
            self._store.writes.put((content, kwargs))

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None,
                  turn_author: Optional[Dict[str, Any]] = None) -> None:
        now = time.time()
        user_source = "agent" if (turn_author or {}).get("is_bot") else "user"
        # Turns go to the session's own namespace, never the default: a whole
        # turn is a record of what was said, and the default namespace is for
        # what matters everywhere. Claims worth sharing are notes.
        where = [session_namespace(session_id or self._session_id)]
        self._enqueue(user_content, source=user_source, namespaces=where, at=now,
                      episode=session_id or None)
        self._enqueue(assistant_content, source="agent", namespaces=where, at=now + 1e-3,
                      episode=session_id or None)

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Hermes' built-in memory is the agent's own notebook: recorded as low-weight
        evidence, never a replacement for what the user said."""
        if action in ("add", "replace"):
            self._enqueue(content, source="host_memory",
                          episode=(metadata or {}).get("session_id"))

    # -- tools --------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return TOOLS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self._reader is None:
            return json.dumps({"error": "tapestry isn't running; nothing was searched or "
                                        f"saved ({self._init_failed or 'not initialized'})"})
        with self._lock:
            return tools.Session(self._reader, self._open, escape=fence_safe).call(tool_name, args)
