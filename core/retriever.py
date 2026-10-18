"""Local knowledge base: embeds documents once, caches vectors on disk, searches with FAISS.
Also builds a BM25 index over the same documents (P2-FR-03) - cheap enough at this corpus size
to always build, only *used* when HYBRID_BM25_ENABLED is set (core/lc_retriever.py)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from core.embeddings import Embedder
from core.text import tokenize
from core.vector_store import VectorStore

log = logging.getLogger("contexta.retriever")


class Retriever:
    def __init__(self, embedder: Embedder, cache_path: Path):
        self.embedder = embedder
        self.cache_path = cache_path
        self.store = VectorStore(embedder.dim)
        self._bm25: BM25Okapi | None = None
        self._bm25_metas: list[dict] = []

    # Persistent embedding cache, keyed by content hash: editing a document re-embeds only that one.
    def _load_cache(self) -> dict[str, np.ndarray]:
        try:
            with np.load(self.cache_path, allow_pickle=False) as z:
                return dict(zip(z["keys"].tolist(), z["vecs"]))
        except Exception:  # missing/corrupt cache is fine
            return {}

    def _save_cache(self, cache: dict[str, np.ndarray]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_name(self.cache_path.stem + ".tmp.npz")
            np.savez(tmp, keys=np.array(list(cache)), vecs=np.vstack(list(cache.values())))
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            log.warning("Could not write embedding cache: %s", exc)

    def build(self, docs: list[dict]) -> None:
        texts = [f"{d['title']}. {d['content']}" for d in docs]
        tag = f"{self.embedder.backend}:{self.embedder.model_name}:"
        keys = [hashlib.sha1((tag + t).encode()).hexdigest() for t in texts]
        cache = self._load_cache()
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing:
            log.info("Embedding %d/%d documents (rest from cache)", len(missing), len(docs))
            vecs = self.embedder.embed_sync([texts[i] for i in missing])
            for i, v in zip(missing, vecs):
                cache[keys[i]] = v
            self._save_cache({k: cache[k] for k in keys})
        self.store = VectorStore(self.embedder.dim)
        self.store.add(np.vstack([cache[k] for k in keys]), [{**d, "text": t} for d, t in zip(docs, texts)])
        self._bm25_metas = [{**d, "text": t} for d, t in zip(docs, texts)]
        self._bm25 = BM25Okapi([tokenize(t) for t in texts]) if texts else None

    @property
    def size(self) -> int:
        return len(self.store)

    def search(self, query_vec: np.ndarray, k: int) -> list[tuple[dict, float]]:
        return self.store.search(query_vec, k)

    def bm25_search(self, query: str, k: int) -> list[tuple[dict, float]]:
        """Only ever called behind HYBRID_BM25_ENABLED (core/lc_retriever.py)."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        top = np.argsort(-scores)[:k]
        return [(self._bm25_metas[i], float(scores[i])) for i in top if scores[i] > 0]
