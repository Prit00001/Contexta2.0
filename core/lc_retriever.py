"""LangChain retriever adapters over the existing retrieval stack (FR-04, FR-07, FR-14).

``ResearchRetriever`` wraps the FAISS paper library (``core.retriever.Retriever``) and the live
arXiv client, and owns the hybrid ranking that used to live in ``SearchService`` (moved here
unchanged: 0.8 * cosine + 0.2 * keyword overlap, de-duplicated by arXiv ID or title, cut at
``MIN_SCORE``). ``PdfChunkRetriever`` ranks the chunks of one uploaded PDF.

Both are ``BaseRetriever`` subclasses, so ``await retriever.ainvoke(question, top_k=3)`` returns
LangChain ``Document``s for interop. The workflow uses :meth:`aretrieve`, which returns the same
sources plus the user-facing warnings and timings that the ``Document`` interface cannot carry.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from core.sources import normalize_source, to_document
from core.text import keywords, lexical_score

log = logging.getLogger("contexta.retrieval")
_ARXIV_ID = re.compile(r"arxiv\.org/abs/([\w.\-/]+?)(v\d+)?/?$")
ARXIV_UNAVAILABLE = "Live arXiv search was unavailable, so only library papers were used."


@dataclass
class RetrievalResult:
    sources: list[dict]
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)


def hybrid_score(terms: set[str], text: str, cos: float) -> float:
    return 0.8 * cos + 0.2 * lexical_score(terms, text)  # semantic + keyword


def _dedupe_key(s: dict) -> str:
    m = _ARXIV_ID.search(str(s.get("url", "")).lower())
    return f"arxiv:{m.group(1)}" if m else re.sub(r"\W+", "", str(s.get("title", "")).lower())


def rrf_fuse(rankings: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion (P2-FR-03): combines rankings by position, not raw score, so a
    cosine-based ranking and a BM25-based ranking (different, incomparable scales) fuse cleanly.
    An item missing from one ranking simply gets no contribution from it, not a penalty rank."""
    rrf_scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for ranking in rankings:
        for pos, item in enumerate(ranking, start=1):
            key = _dedupe_key(item)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + pos)
            items.setdefault(key, item)
    fused = [{**items[key], "score": s} for key, s in rrf_scores.items()]
    return sorted(fused, key=lambda x: x["score"], reverse=True)


def rank(candidates: list[tuple[dict, float]], question: str) -> list[dict]:
    terms = set(keywords(question, 8))
    best: dict[str, dict] = {}
    for src, cos in candidates:
        cand = {**src, "score": hybrid_score(terms, src["text"], cos)}
        k = _dedupe_key(cand)
        if k not in best or cand["score"] > best[k]["score"]:
            best[k] = cand
    return sorted(best.values(), key=lambda x: x["score"], reverse=True)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.clip(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12, None)


class _AsyncOnlyRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def aretrieve(self, question: str, top_k: int, **options: Any) -> RetrievalResult:  # pragma: no cover
        raise NotImplementedError

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun, **kwargs: Any
    ) -> list[Document]:
        top_k = int(kwargs.pop("top_k", 5))
        result = await self.aretrieve(query, top_k, **kwargs)
        return [to_document(s) for s in result.sources]

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun, **kwargs: Any
    ) -> list[Document]:
        raise NotImplementedError("Contexta retrievers are async; use ainvoke()/aretrieve().")


class ResearchRetriever(_AsyncOnlyRetriever):
    """Paper library (FAISS) + optional live arXiv, merged and re-ranked."""

    embedder: Any
    library: Any  # core.retriever.Retriever
    arxiv: Any  # services.arxiv_client.ArxivClient
    arxiv_enabled: bool = True
    arxiv_budget_s: float = 9.0
    min_score: float = 0.2
    hybrid_bm25_enabled: bool = False  # P2-FR-03
    reranker: Any = None  # core.reranker.Reranker (P2-07); None = no reranking
    rerank_candidates: int = 20
    # Ranking prior for the curated library over live arXiv (LIBRARY_BOOST / RERANK_LIBRARY_BOOST): added
    # to a library source's ranking score only - MIN_SCORE still judges its raw score, and the reported
    # `score` stays raw. EVALUATION_REPORT 5g.
    library_boost: float = 0.0
    rerank_library_boost: float = 0.0

    async def _arxiv_candidates(self, question: str, qvec: np.ndarray) -> list[tuple[dict, float]]:
        papers = await self.arxiv.search(question)
        if not papers:
            return []
        vecs = await self.embedder.embed([p["text"] for p in papers])
        return [(p, float(v @ qvec)) for p, v in zip(papers, vecs, strict=True)]

    async def aretrieve(self, question: str, top_k: int, use_arxiv: bool = True, **_: Any) -> RetrievalResult:
        warnings: list[str] = []
        t0 = time.perf_counter()
        qvec = (await self.embedder.embed([question]))[0]
        reranking = self.reranker is not None and self.reranker.available
        candidates = self.library.search(qvec, max(top_k * 2, self.rerank_candidates if reranking else 0))
        t_local = time.perf_counter()
        if use_arxiv and self.arxiv_enabled:
            try:
                candidates += await asyncio.wait_for(self._arxiv_candidates(question, qvec), self.arxiv_budget_s)
            except Exception as exc:  # noqa: BLE001 - arXiv is best-effort: timeout, HTTP error, bad XML...
                log.warning(
                    "arXiv unavailable: %s",
                    type(exc).__name__,
                    extra={"event": "arxiv.search", "status": "error", "category": "retrieval_unavailable"},
                )
                warnings.append(ARXIV_UNAVAILABLE)
        t_arxiv = time.perf_counter()
        hybrid_ranked = [s for s in rank(candidates, question) if s["score"] >= self.min_score]
        if self.library_boost and not reranking:  # with reranking on, the prior is applied after reranking
            hybrid_ranked.sort(
                key=lambda s: s["score"] + (self.library_boost if s["source"] == "local" else 0.0), reverse=True
            )
        if self.hybrid_bm25_enabled and hybrid_ranked:
            # BM25 only *re-ranks* candidates that already passed the dense quality gate above -
            # it never admits a candidate the dense signal rejected (a weak keyword match like
            # common-word overlap must not resurrect something min_score correctly filtered out).
            # ADR-003.
            passed_keys = {_dedupe_key(s) for s in hybrid_ranked}
            bm25_hits = self.library.bm25_search(question, top_k * 2)
            bm25_ranked = [{**d, "score": s} for d, s in bm25_hits if _dedupe_key(d) in passed_keys]
            ordered = rrf_fuse([hybrid_ranked, bm25_ranked])
        else:
            ordered = hybrid_ranked
        timings = {"local": round((t_local - t0) * 1000), "arxiv": round((t_arxiv - t_local) * 1000)}
        if reranking and ordered:
            # Reorders only what already passed MIN_SCORE (core/reranker.py), never widens it.
            t_rerank = time.perf_counter()
            ranked = await self.reranker.rerank(
                question, ordered[: self.rerank_candidates], top_k, library_boost=self.rerank_library_boost
            )
            timings["rerank"] = round((time.perf_counter() - t_rerank) * 1000)
        else:
            ranked = ordered[:top_k]
        return RetrievalResult(sources=[normalize_source(s) for s in ranked], warnings=warnings, timings_ms=timings)


class PdfChunkRetriever(_AsyncOnlyRetriever):
    """Ranks the chunks of one uploaded PDF. ``chunk_vectors`` are unit-normalised embeddings."""

    embedder: Any
    chunks: list[dict]  # {"page": int, "text": str} from core.pdf.chunk
    chunk_vectors: Any  # np.ndarray (n_chunks, dim)
    filename: str
    document_id: str

    async def aretrieve(self, question: str, top_k: int, **_: Any) -> RetrievalResult:
        t0 = time.perf_counter()
        qvec = _unit(np.asarray((await self.embedder.embed([question]))[0], dtype=np.float32))
        # Rank every chunk; no min_score cut, since the user chose this document deliberately.
        terms = set(keywords(question, 8))
        cos = self.chunk_vectors @ qvec
        pool = np.argsort(-cos)[: max(top_k * 3, 10)]
        scored = sorted(
            (
                {
                    "id": f"pdf-{i}",
                    "document_id": self.document_id,
                    "chunk_id": f"{self.document_id}#{i}",
                    "title": f"{self.filename} · p. {self.chunks[i]['page']}",
                    "source": "pdf",
                    "url": "",
                    "text": self.chunks[i]["text"],
                    "page": self.chunks[i]["page"],
                    "score": hybrid_score(terms, self.chunks[i]["text"], float(cos[i])),
                }
                for i in pool
            ),
            key=lambda x: x["score"],
            reverse=True,
        )
        return RetrievalResult(
            sources=[normalize_source(s) for s in scored[:top_k]],
            timings_ms={"embed": round((time.perf_counter() - t0) * 1000)},
        )
