"""Hermes Agent adapter: tapestry as a Hermes MemoryProvider.

Installed as a pip entry point (group ``hermes_agent.memory_providers``), so
Hermes finds it without a plugin directory and without mnemonic's 8KB
admission-token trap. Select it with ``memory.provider: tapestry``.

This module translates Hermes' lifecycle into tapestry moments
(docs/interfaces/HOOKS.md) and contains no memory logic. Hermes quirks
it absorbs so the core never has to:

- Hermes swallows prefetch exceptions, so a failed recall would look like
  "nothing relevant". Every failure here becomes a visible marker instead.
- Hermes' sanitize_context() deletes <memory-context> tags and "[System note:"
  spans from provider output, including inside memory content. The core keeps
  content verbatim; this adapter neutralizes those sequences on the way out.
- Tool schemas use "parameters" (Hermes ignores "input_schema").
- sync_turn must not block, so writes go through a background writer.
- Hermes has no scheduler of its own for Tapestry, so the session-end hook starts
  background jobs the same way Claude Code's does.
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

from tapestry import guide, jobs, recall, render, tools, unrecorded
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
PREFETCH_BUDGET = render.PROMPT_BUDGET


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
        self.unrecorded = unrecorded.Ledger(Path(db_path).with_name("unrecorded.jsonl"))
        self.state = Path(db_path).with_name("state")
        self.behind = unrecorded.Ledger(self.state / "behind.jsonl")
        self.activity = jobs.Activity(self.state / "active")
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
                what, write = item
                try:
                    write(mind)
                except Exception as e:  # it already happened; the agent is told on its next prefetch
                    logger.error("tapestry could not record %s: %s", what, e)
                    self.unrecorded.add(f"{what} couldn't be recorded ({type(e).__name__}: {e})")
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
START_QUERY = "Where things stand: what was decided, what is unfinished, and who I am working with."


def _config_home() -> Path:
    """Where the owner's choice of model lives, the same for every host on this machine."""
    return Path(os.environ.get("TAPESTRY_HOME") or Path.home() / ".tapestry")


class TapestryProvider(_Base):
    """Tapestry for Hermes: one mind per Hermes profile."""

    def __init__(self, encoder_factory: EncoderFactory | None = None) -> None:
        self._encoder_factory = encoder_factory or _default_encoder
        self._home: Optional[Path] = None
        self._db_path: Optional[Path] = None
        self._encode = None
        self._model_tag = ""
        self._store: Optional[_Store] = None
        self._open: list[str] = []  # topics open this session; the general topic is implicit
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
        if not self._reader:
            return guide.GUIDE
        where = f"\n\nYour tapestry mind is {self._db_path}."
        try:
            with self._lock:
                topics, waiting = self._reader.topics(), jobs.waiting_notice(self._reader, _config_home())
                # A Hermes session has no place, so it starts from where things stand overall.
                auto = recall.session_start(self._reader, START_QUERY, self._open)
            self._record_recalled(auto.highlights)
            shown = render.automatic(auto, PREFETCH_BUDGET)
            start = ("\n\nWhat you remember about where things stand:\n" + fence_safe(shown)) if shown else ""
        except RecallFailed as e:
            topics, waiting, start = [], "", "\n\n" + self._unavailable_marker(f"recall failed ({e})")
        except Exception:
            topics, waiting, start = [], "", ""
        behind = jobs.behind_notice(self._store.behind.take()) if self._store else ""
        notices = "\n".join(n for n in (behind, waiting) if n)
        return ((notices + "\n\n") if notices else "") + guide.GUIDE + where \
            + render.directory(topics, self._open) + start

    # -- recall -------------------------------------------------------------

    def _unavailable_marker(self, why: str) -> str:
        return (f"[MEMORY UNAVAILABLE: {why}. No memories were searched. Don't treat this "
                "turn as evidence that memory holds nothing relevant; it wasn't consulted.]")

    def _record_recalled(self, hits, session_id: str = "") -> None:
        sid = session_id or self._session_id
        if hits and sid:
            ids, now = [h.id for h in hits], time.time()
            self._enqueue("what was recalled", lambda m: m.episode(sid).add_recalled(ids, now))

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._last_count = 0
        if is_trivial_prompt(query):
            return ""
        if self._reader is None:
            return self._unavailable_marker(f"tapestry failed to start ({self._init_failed})") \
                if self._init_failed else ""
        self._store.activity.touch(session_id or self._session_id)
        gap = unrecorded.notice(self._store.unrecorded.take()) if self._store else ""
        gap = gap + "\n" if gap else ""
        try:
            with self._lock:
                auto = recall.before_turn(self._reader, query, self._open)
        except RecallFailed as e:
            logger.warning("tapestry recall failed (surfacing): %s", e)
            return gap + self._unavailable_marker(f"recall failed ({e})")
        self._last_count = len(auto.highlights)
        self._record_recalled(auto.highlights, session_id)
        return gap + fence_safe(render.automatic(auto, PREFETCH_BUDGET))

    def recall_status(self):
        if RecallStatus is None or not self._last_count:
            return None
        return RecallStatus(provider_label="tapestry", count=self._last_count)

    # -- remember -----------------------------------------------------------

    def _enqueue(self, what: str, write: Callable[[Mind], Any]) -> None:
        if self._write_mode and self._store is not None:
            self._store.writes.put((what, write))

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None,
                  turn_author: Optional[Dict[str, Any]] = None) -> None:
        """Add both sides of a turn to the session's episode, never as memories: facts are
        drawn from the episode later (docs/primitives/EPISODE.md). A Hermes session has no
        place (docs/interfaces/HOOKS.md)."""
        sid, now = session_id or self._session_id, time.time()
        if not sid:
            return
        user_source = "agent" if (turn_author or {}).get("is_bot") else "user"

        def add(mind: Mind) -> None:
            ep = mind.episode(sid)
            ep.add_turn(f"hermes:{sid}:{now!r}:user", user_source, user_content or "", now)
            ep.add_turn(f"hermes:{sid}:{now!r}:agent", "agent", assistant_content or "", now + 1e-3)
        self._enqueue("a turn", add)

    def on_session_end(self, messages: Optional[List[Dict[str, Any]]] = None, **kwargs) -> None:
        """End the episode, make it durable, then start background jobs, as Claude Code's hook does."""
        if not self._write_mode or self._store is None or not self._session_id:
            return
        sid = self._session_id
        self._enqueue("the end of the session", lambda m: m.episode(sid).end())
        self._store.flush(timeout=30)
        self._store.activity.end(sid)
        try:
            jobs.start(self._store.state / "jobs.log", "--mind", str(self._db_path),
                       "--state", str(self._store.state))
        except Exception as e:
            self._store.behind.add(f"couldn't start background work ({type(e).__name__}: {e})")

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Hermes' built-in memory is the agent's own notebook: recorded as low-weight
        evidence, never a replacement for what the user said."""
        if action in ("add", "replace") and content and content.strip():
            episode = (metadata or {}).get("session_id") or None
            self._enqueue("a note from Hermes' memory",
                          lambda m: m.remember(content, source="host_memory", episode=episode))

    # -- tools --------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return TOOLS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self._reader is None:
            return json.dumps({"error": "tapestry isn't running; nothing was searched or "
                                        f"saved ({self._init_failed or 'not initialized'})"})
        with self._lock:
            episode = self._reader.episode(self._session_id) if self._session_id else None
            return tools.Session(self._reader, self._open, episode=episode,
                                 escape=fence_safe).call(tool_name, args)
