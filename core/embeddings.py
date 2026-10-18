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
import os
import threading

import numpy as np

from core.text import tokenize

# ONNX Runtime (used by fastembed) otherwise sends usage telemetry to mobile.events.data.microsoft.com
# - observed during model loading in Phase 1 testing. Must be set before onnxruntime is imported;
# operators can opt back in with ORT_DISABLE_TELEMETRY=0.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

log = logging.getLogger("contexta.embeddings")
HASH_DIM = 384


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return (m / np.clip(norms, 1e-9, None)).astype("float32")


def _hash_embed(texts: list[str]) -> np.ndarray:
    out = np.zeros((len(texts), HASH_DIM), dtype="float32")
    for row, text in enumerate(texts):
        toks = tokenize(text)
        for feat in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]:
            h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
            out[row, h % HASH_DIM] += 1.0 if (h >> 64) & 1 else -1.0
    return _normalize(out)


def _load_fastembed(model_name: str, cache_dir: str | None):
    """Load a fastembed model, failing fast when it is neither cached nor downloadable (P2-FR-02).

    fastembed 0.7 does not pass ``TextEmbedding(retries=...)`` through to its downloader, which
    always makes 3 attempts sleeping 3 + 9 + 27 s - ~42 s offline (measured). So the download is
    done here with one attempt (~3.7 s offline, measured; fastembed still sleeps 3 s after it) and
    the model is loaded from the resulting path. A model already in the cache is loaded without
    any network call.
    """
    from pathlib import Path

    from fastembed import TextEmbedding
    from fastembed.common.utils import define_cache_dir

    for impl in TextEmbedding.EMBEDDINGS_REGISTRY:
        if any(m.model.lower() == model_name.lower() for m in impl._list_supported_models()):
            break
    else:
        raise ValueError(f"model {model_name} is not supported by fastembed")
    desc = impl._get_model_description(model_name)
    root = Path(define_cache_dir(cache_dir))
    cached = [root / f"models--{desc.sources.hf.replace('/', '--')}"] if desc.sources.hf else []
    cached.append(root / f"{'fast-' if desc.sources.deprecated_tar_struct else ''}{desc.model.split('/')[-1]}")
    # Only probe the cache when it plausibly holds the model: a local-only miss also costs a 3 s sleep.
    local_only = any(p.is_dir() and any(p.iterdir()) for p in cached)
    path = impl.download_model(desc, str(root), retries=1, local_files_only=local_only)
    return TextEmbedding(model_name=model_name, cache_dir=cache_dir, specific_model_path=str(path))


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
                self._model = _load_fastembed(model_name, cache_dir)
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
