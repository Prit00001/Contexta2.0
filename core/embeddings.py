"""Embedding wrapper.

Default backend is fastembed (ONNX runtime, same all-MiniLM-L6-v2 model as before) which
needs ~150 MB RAM instead of the ~1 GB that torch + sentence-transformers wants. That is
what makes the free 512 MB tier viable. A deterministic hashing backend exists for tests
and as an emergency fallback if the model cannot be loaded.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading

import numpy as np

from core.text import tokenize

log = logging.getLogger("contexta.embeddings")
HASH_DIM = 384


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return (m / np.clip(norms, 1e-9, None)).astype("float32")


def _hash_embed(texts: list[str]) -> np.ndarray:
    out = np.zeros((len(texts), HASH_DIM), dtype="float32")
    for row, text in enumerate(texts):
        toks = tokenize(text)
        for feat in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
            h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
            out[row, h % HASH_DIM] += 1.0 if (h >> 64) & 1 else -1.0
    return _normalize(out)


class Embedder:
    def __init__(self, backend: str, model_name: str, cache_dir: str | None = None):
        self.requested_backend = backend  # what config asked for; differs from .backend after a fallback
        self.backend = backend
        self.model_name = model_name
        self.dim = HASH_DIM
        self._model = None
        self._lock = threading.Lock()
        if backend == "fastembed":
            try:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
            except Exception as exc:  # noqa: BLE001
                log.error("fastembed failed to load (%s); using hash embeddings", exc)
                self.backend = "hash"
        else:
            self.backend = "hash"
        self.dim = self.embed_sync(["probe"]).shape[1]
        log.info("Embedder ready: backend=%s model=%s dim=%d", self.backend, model_name, self.dim)

    @property
    def degraded(self) -> bool:
        """True when a real model was requested but hash embeddings are in use (defect D-03)."""
        return self.backend != self.requested_backend

    def embed_sync(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        if self._model is None:
            return _hash_embed(texts)
        with self._lock:
            vecs = np.vstack([np.asarray(v, dtype="float32") for v in self._model.embed(texts)])
        return _normalize(vecs)

    async def embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embed_sync, texts)
