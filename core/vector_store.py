"""FAISS IndexFlatIP wrapper (exact cosine search on normalized vectors).
Falls back to a numpy matmul if faiss isn't installed - same results, no extra dependency."""

from __future__ import annotations

import numpy as np


class VectorStore:
    def __init__(self, dim: int):
        self.dim = dim
        self.metas: list[dict] = []
        try:
            import faiss

            self._index = faiss.IndexFlatIP(dim)
            self._mat = None
        except ImportError:  # pragma: no cover
            self._index = None
            self._mat = np.zeros((0, dim), dtype="float32")

    def __len__(self) -> int:
        return len(self.metas)

    def add(self, vectors: np.ndarray, metas: list[dict]) -> None:
        vectors = np.ascontiguousarray(vectors, dtype="float32")
        if self._index is not None:
            self._index.add(vectors)
        else:  # pragma: no cover
            self._mat = np.vstack([self._mat, vectors])
        self.metas.extend(metas)

    def search(self, query: np.ndarray, k: int) -> list[tuple[dict, float]]:
        k = min(k, len(self.metas))
        if k == 0:
            return []
        q = np.ascontiguousarray(query.reshape(1, -1), dtype="float32")
        if self._index is not None:
            scores, idx = self._index.search(q, k)
            pairs = zip(idx[0], scores[0], strict=True)
        else:  # pragma: no cover
            s = (self._mat @ q.T).ravel()
            top = np.argsort(-s)[:k]
            pairs = zip(top, s[top], strict=True)
        return [(self.metas[i], float(s)) for i, s in pairs if i >= 0]
