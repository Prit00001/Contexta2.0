"""Orchestration for the public research endpoints: answer cache, PDF-embedding cache, and the
mapping from LangGraph state to the API response.

The pipeline itself - validate, retrieve (FAISS library + arXiv, or an uploaded PDF), generate,
validate citations, fall back - lives in ``services.research_graph``; retrieval and ranking live
in ``core.lc_retriever``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict

import numpy as np
from fastapi import HTTPException

from app.config import Settings
from core.cache import InMemoryAnswerCache, cache_key
from core.embeddings import Embedder
from core.generator import Generator
from core.lc_retriever import PdfChunkRetriever, ResearchRetriever, _unit
from core.retriever import Retriever
from core.text import clip
from services.arxiv_client import ArxivClient
from services.research_graph import build_research_graph, run_research

log = logging.getLogger("contexta.search")

NO_SOURCES_MSG = (
    "I couldn't find relevant sources for that question. Try rephrasing it with more specific ML/NLP terms."
)
NO_PDF_TEXT_MSG = "No readable text was found in this PDF."
_GEN_STAGES = ("generate", "validate_answer", "fallback", "insufficient_evidence")


def _source_out(s: dict, marker: int) -> dict:
    snippet = s["text"] if s["source"] == "pdf" else s["text"].split(". ", 1)[-1]
    return {
        "id": s["id"], "title": s["title"], "source": s["source"], "url": s.get("url", ""),
        "score": round(s["score"], 4), "snippet": clip(snippet, 300),
        "authors": s.get("authors", []), "published": s.get("published", ""), "page": s.get("page"),
        "marker": marker, "document_id": s["document_id"], "chunk_id": s["chunk_id"],
    }  # fmt: skip


class SearchService:
    def __init__(
        self,
        settings: Settings,
        embedder: Embedder,
        retriever: Retriever,
        arxiv: ArxivClient,
        generator: Generator,
        answer_cache=None,
    ):
        self.s = settings
        self.embedder = embedder
        self.retriever = retriever
        self.arxiv = arxiv
        self.generator = generator
        self.graph = build_research_graph(generator, settings.llm_max_attempts)
        self.research_retriever = ResearchRetriever(
            embedder=embedder, library=retriever, arxiv=arxiv, arxiv_enabled=settings.arxiv_enabled,
            arxiv_budget_s=settings.arxiv_budget_s, min_score=settings.min_score,
            hybrid_bm25_enabled=settings.hybrid_bm25_enabled,
        )  # fmt: skip
        # core.cache: Redis-backed (shared across instances) when REDIS_URL is set, else per process
        self.cache = answer_cache if answer_cache is not None else InMemoryAnswerCache(settings.answer_cache_ttl)
        # Per-instance cache of PDF chunk embeddings, so follow-up questions on the same
        # file skip re-embedding when they land on a warm instance. Best-effort on serverless.
        self._pdf_vecs: OrderedDict[str, np.ndarray] = OrderedDict()

    # ---- cache ---------------------------------------------------------------
    async def _cached(self, key: tuple) -> dict | None:
        hit = await self.cache.get(cache_key(key))
        return {**hit, "cached": True} if hit else None

    async def _store(self, key: tuple, response: dict) -> None:
        if response["llm_used"] or not response["sources"]:  # don't pin a degraded answer in cache
            await self.cache.set(cache_key(key), response)

    # ---- graph -> response ---------------------------------------------------
    async def _run(self, retriever, question: str, top_k: int, options: dict, empty_message: str) -> tuple[dict, dict]:
        state = await run_research(self.graph, retriever, question, top_k, options, empty_message)
        if state.get("outcome") == "error":
            if state.get("error") == "retrieval_failed":
                raise HTTPException(503, "Retrieval is temporarily unavailable. Please try again.")
            raise HTTPException(422, state.get("error") or "Invalid request.")
        sources = state.get("sources", [])
        report = state["citation_report"]
        response = {
            "question": state["question"],
            "answer": state["answer"],
            "provider": state.get("provider", "none"),
            "model_used": state.get("model", "none"),
            "llm_used": bool(state.get("llm_used")),
            "sources": [_source_out(s, i) for i, s in enumerate(sources, 1)],
            "total_sources": len(sources),
            "cached": False,
            "warnings": list(dict.fromkeys(state.get("warnings", []))),
            "diagnostics": list(dict.fromkeys(state.get("diagnostics", []))),
            "outcome": state["outcome"],
            "citations": report.citations,
            "claims": report.claims,
            "citation_check": report.summary(),
            "workflow": state["trace"],
        }
        timings = {
            "retrieval": state.get("retrieval_timings", {}),
            "generate_ms": sum(t["duration_ms"] for t in state["trace"] if t["stage"] in _GEN_STAGES),
        }
        return response, timings

    # ---- public API ----------------------------------------------------------
    async def ask(self, question: str, top_k: int, use_arxiv: bool) -> dict:
        question = " ".join(question.split())
        use_arxiv = use_arxiv and self.s.arxiv_enabled
        key = ("ask", question.lower(), top_k, use_arxiv)
        if hit := await self._cached(key):
            return hit

        t0 = time.perf_counter()
        response, t = await self._run(
            self.research_retriever, question, top_k, {"use_arxiv": use_arxiv}, NO_SOURCES_MSG
        )
        response["timings_ms"] = {
            "local": t["retrieval"].get("local", 0),
            "arxiv": t["retrieval"].get("arxiv", 0),
            "generate": t["generate_ms"],
            "total": round((time.perf_counter() - t0) * 1000),
        }
        await self._store(key, response)
        return response

    async def ask_pdf(self, question: str, chunks: list[dict], top_k: int, filename: str) -> dict:
        """Answer from an uploaded PDF. `chunks` are {"page": int, "text": str} from core.pdf.chunk."""
        question = " ".join(question.split())
        warnings: list[str] = []
        if len(chunks) > self.s.max_pdf_chunks:
            chunks = chunks[: self.s.max_pdf_chunks]
            warnings.append(f"This PDF is long, so only pages 1–{chunks[-1]['page']} were searched.")

        doc_key = hashlib.sha1("\x00".join(c["text"] for c in chunks).encode()).hexdigest()
        key = ("pdf", doc_key, question.lower(), top_k)
        if hit := await self._cached(key):
            return hit

        t0 = time.perf_counter()
        vecs = self._pdf_vecs.get(doc_key)
        if vecs is None:
            vecs = _unit(np.asarray(await self.embedder.embed([c["text"] for c in chunks]), dtype=np.float32))
            self._pdf_vecs[doc_key] = vecs
            while len(self._pdf_vecs) > 8:
                self._pdf_vecs.popitem(last=False)
        else:
            self._pdf_vecs.move_to_end(doc_key)
        t_embed = time.perf_counter()
        retriever = PdfChunkRetriever(
            embedder=self.embedder, chunks=chunks, chunk_vectors=vecs, filename=filename,
            document_id=f"pdf:{doc_key[:12]}",
        )  # fmt: skip
        response, t = await self._run(retriever, question, top_k, {}, NO_PDF_TEXT_MSG)
        response["warnings"] = warnings + response["warnings"]
        response["timings_ms"] = {
            "embed": round((t_embed - t0) * 1000) + t["retrieval"].get("embed", 0),
            "generate": t["generate_ms"],
            "total": round((time.perf_counter() - t0) * 1000),
        }
        await self._store(key, response)
        return response
