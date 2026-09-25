"""On-disk embedding cache, so repeated benchmark runs don't re-embed.

LongMemEval-S alone has ~19k distinct sessions averaging ~10k characters. The
cache is keyed by model tag and text hash, and lives under bench/data/ (not
committed). A cache for one model is never read by another.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


class EmbedCache:
    def __init__(self, model_tag: str, directory: Path = CACHE_DIR) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in model_tag)
        self._db = sqlite3.connect(directory / f"embed-{safe}.sqlite")
        self._db.execute("CREATE TABLE IF NOT EXISTS e (k TEXT PRIMARY KEY, v BLOB)")

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def encode(self, texts: list[str], encode_fn) -> np.ndarray:
        """Embeddings for `texts`, computing only the ones not cached."""
        keys = [self._key(t) for t in texts]
        found: dict[str, np.ndarray] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = f"SELECT k, v FROM e WHERE k IN ({','.join('?' * len(chunk))})"
            found.update((k, np.frombuffer(v, dtype="<f4")) for k, v in self._db.execute(q, chunk))
        missing = [i for i, k in enumerate(keys) if k not in found]
        if missing:
            vecs = encode_fn([texts[i] for i in missing])
            with self._db:
                self._db.executemany("INSERT OR REPLACE INTO e VALUES (?, ?)",
                                     [(keys[i], np.asarray(v, dtype="<f4").tobytes())
                                      for i, v in zip(missing, vecs)])
            for i, v in zip(missing, vecs):
                found[keys[i]] = np.asarray(v, dtype="<f4")
        return np.vstack([found[k] for k in keys]) if keys else np.zeros((0, 0), "<f4")

    def close(self) -> None:
        self._db.close()
