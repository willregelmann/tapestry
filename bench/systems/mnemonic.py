"""The current Hermes provider, loaded from its install, as the Stage 0 baseline.

This runs mnemonic's own retriever against a throwaway database that uses
Hermes' holographic schema. That's the schema mnemonic migrates in production.
Nothing touches the real store. Locations can be overridden:

    TAPESTRY_MNEMONIC_DIR   default ~/.hermes/plugins/mnemonic
    TAPESTRY_MNEMONIC_MODEL default ~/.hermes/mnemonic-model

mnemonic knows nothing about scopes or supersession, so it's scored as it
would behave: every memory is searchable and none is ever retired.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from bench.fixture import Memory
from bench.metrics import Hit

# The subset of plugins/memory/holographic/store.py that mnemonic reads.
_HOLOGRAPHIC_SCHEMA = """
CREATE TABLE facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT 'general',
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hrr_vector      BLOB
);
CREATE VIRTUAL TABLE facts_fts USING fts5(content, tags, content=facts, content_rowid=fact_id);
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
"""


def _plugin_dir() -> Path:
    return Path(os.environ.get("TAPESTRY_MNEMONIC_DIR",
                               Path.home() / ".hermes/plugins/mnemonic"))


def _model_dir() -> Path:
    return Path(os.environ.get("TAPESTRY_MNEMONIC_MODEL",
                               Path.home() / ".hermes/mnemonic-model"))


def _load_module():
    src = _plugin_dir() / "__init__.py"
    if not src.is_file():
        raise FileNotFoundError(f"mnemonic not found at {src}")
    spec = importlib.util.spec_from_file_location("_bench_mnemonic", src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod, hashlib.sha256(src.read_bytes()).hexdigest()[:12]


class Mnemonic:
    name = "mnemonic"

    def __init__(self) -> None:
        self._mod, self._src_sha = _load_module()
        md = _model_dir()
        self._enc = self._mod.Encoder(md / "model.onnx", md / "tokenizer.json")
        self._tmp: Path | None = None
        self._ret = None
        self._ids: dict[int, str] = {}

    def config(self) -> dict:
        return {"source_sha": self._src_sha, "model": self._mod.MODEL_TAG,
                "band_withhold": self._mod.BAND_WITHHOLD,
                "band_confident": self._mod.BAND_CONFIDENT,
                "scopes": "ignored", "supersession": "ignored"}

    def seed(self, memories: list[Memory]) -> None:
        self.close()
        self._tmp = Path(tempfile.mkdtemp(prefix="tapestry-bench-"))
        db = self._tmp / "memory_store.db"
        with sqlite3.connect(db) as conn:
            conn.executescript(_HOLOGRAPHIC_SCHEMA)
        store = self._mod.MnemonicStore(db)
        self._ids = {}
        for mem in memories:
            fid = store.save_fact(mem.content, tags=mem.tags)
            self._ids[fid] = mem.id
        pending = store.rows_needing_embedding()
        if pending:
            vecs = self._enc.encode([r["content"] for r in pending])
            for row, v in zip(pending, vecs):
                store.write_embedding(row["fact_id"], v)
        self._ret = self._mod.MnemonicRetriever(store, self._enc, band_log=None)

    def recall(self, text, *, scopes, k, at):
        return [Hit(id=self._ids[h["fact_id"]], score=h["cosine"],
                    label=f'{h["band"]}/{h["via"]}',
                    confident=h["band"] == "ok" and h["via"] == "cosine")
                for h in self._ret.search(text, limit=k)]

    def close(self) -> None:
        if self._ret is not None:
            self._ret.store._conn.close()
            self._ret = None
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
