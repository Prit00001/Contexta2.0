"""Optional cross-encoder reranking (backlog P2-07), off unless RERANK_MODEL is set.

The retriever over-fetches ``RERANK_CANDIDATES`` sources that already passed ``MIN_SCORE``; a small
ONNX cross-encoder (fastembed ``TextCrossEncoder``, e.g. ``Xenova/ms-marco-MiniLM-L-6-v2``, ~80 MB)
scores each (question, passage) pair and the top ``top_k`` by that score are kept. Reranking only
reorders - it never admits a source the retrieval quality gate rejected, the same rule BM25 fusion
follows (ADR-003). Each source keeps its retrieval ``score`` (MIN_SCORE's scale) and gains
``rerank_score``.

If the model can't be loaded (offline, unknown name) reranking is disabled with an error log and
``/ready`` reports it; retrieval keeps working without it, like the embedding fallback.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from core.embeddings import _load_fastembed

log = logging.getLogger("contexta.reranker")


class Reranker:
    def __init__(self, model_name: str, cache_dir: str | None = None, model=None):
        self.model_name = model_name
        self._lock = threading.Lock()
        self._model = model  # tests inject a fake with .rerank(query, documents) -> iterable of floats
        self.error = ""
        if self._model is None:
            try:
                self._model = _load_fastembed(model_name, cache_dir, kind="cross_encoder")
            except Exception as exc:  # noqa: BLE001 - reranking is optional; never block startup
                self.error = type(exc).__name__
                log.error("reranker %s failed to load (%s); reranking disabled", model_name, exc)

    @property
    def available(self) -> bool:
        return self._model is not None

    def _scores(self, query: str, texts: list[str]) -> list[float]:
        with self._lock:
            return [float(x) for x in self._model.rerank(query, texts)]

    async def rerank(self, query: str, sources: list[dict], top_k: int) -> list[dict]:
        if not self.available or len(sources) < 2:
            return sources[:top_k]
        scores = await asyncio.to_thread(self._scores, query, [s["text"] for s in sources])
        ranked = sorted(
            ({**s, "rerank_score": round(sc, 4)} for s, sc in zip(sources, scores, strict=True)),
            key=lambda s: s["rerank_score"],
            reverse=True,
        )
        return ranked[:top_k]
