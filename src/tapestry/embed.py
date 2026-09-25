"""Sentence embeddings: all-MiniLM-L6-v2, int8, on ONNX Runtime. No torch.

Kept deliberately identical to what mnemonic measured (BANDS.md): chunks of
256 tokens, mean-pooled within a chunk, averaged across chunks, L2-normalized.
The model and tokenizer are pinned by sha256, because a silently swapped model
is a silently corrupted mind: vectors from two encoders can't be compared.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

DIM = 384
MODEL_TAG = "MiniLM-L6-v2-int8@4278337fd0ff"
MODEL_SHA = "4278337fd0ff3c68bfb6291042cad8ab363e1d9fbc43dcb499fe91c871902474"
TOKENIZER_SHA = "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"
CHUNK = 256


def default_model_dir() -> Path:
    env = os.environ.get("TAPESTRY_MODEL_DIR")
    if env:
        return Path(env)
    own = Path.home() / ".tapestry" / "model"
    if (own / "model.onnx").is_file():
        return own
    return Path.home() / ".hermes" / "mnemonic-model"  # shared with mnemonic, same sha


class Encoder:
    tag = MODEL_TAG

    def __init__(self, model_dir: Path | None = None, threads: int = 2) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = Path(model_dir or default_model_dir())
        model, tok = d / "model.onnx", d / "tokenizer.json"
        for path, want in ((model, MODEL_SHA), (tok, TOKENIZER_SHA)):
            if not path.is_file():
                raise FileNotFoundError(f"missing {path.name} in {d}")
            got = hashlib.sha256(path.read_bytes()).hexdigest()
            if got != want:
                raise RuntimeError(f"{path.name} sha256 {got[:12]} != pinned {want[:12]}")
        # tokenizer.json ships with truncation and padding baked in at 128;
        # both must be cleared or long memories are silently clipped.
        self._tok = Tokenizer.from_file(str(tok))
        self._tok.no_truncation()
        self._tok.no_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}

    def _forward(self, rows: list[list[int]]) -> np.ndarray:
        width = max(len(r) for r in rows)
        ids = np.zeros((len(rows), width), dtype=np.int64)
        mask = np.zeros_like(ids)
        for i, r in enumerate(rows):
            ids[i, :len(r)] = r
            mask[i, :len(r)] = 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = self._sess.run(None, feed)[0]
        m = mask[..., None].astype(np.float32)
        pooled = (out * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
        return pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)

    def __call__(self, texts: list[str]) -> np.ndarray:
        vecs = []
        for t in texts:
            ids = self._tok.encode(t).ids
            chunks = [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)] or [ids]
            chunks = [c for c in chunks if len(c) >= 8] or [ids[:CHUNK]]
            v = self._forward(chunks).mean(0)
            vecs.append(v / np.linalg.norm(v))
        return np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, DIM), np.float32)


MODEL_URLS = {
    "model.onnx": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model_qint8_arm64.onnx",
    "tokenizer.json": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer.json",
}


def fetch_model(dest: Path | None = None) -> Path:
    """Download the pinned model into ~/.tapestry/model (or `dest`), verifying sha256."""
    import urllib.request

    d = Path(dest or Path.home() / ".tapestry" / "model")
    d.mkdir(parents=True, exist_ok=True)
    for name, want in (("model.onnx", MODEL_SHA), ("tokenizer.json", TOKENIZER_SHA)):
        target = d / name
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == want:
            continue
        tmp = target.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URLS[name], tmp)
        got = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if got != want:
            tmp.unlink()
            raise RuntimeError(f"{name} from {MODEL_URLS[name]} has sha256 {got[:12]}, "
                               f"expected {want[:12]}; refusing to use it")
        tmp.replace(target)
    return d
