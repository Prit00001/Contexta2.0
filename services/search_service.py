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
from services.research_graph import build_research_graph, run_research, stream_research

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
        reranker=None,
    ):
        self.s = settings
        self.embedder = embedder
        self.retriever = retriever
        self.arxiv = arxiv
        self.generator = generator
        self.reranker = reranker
        rewriter = (
            Generator(settings.rewrite_providers, generator.client, settings.llm_timeout)
            if settings.rewrite_providers
            else generator
        )
        self.graph = build_research_graph(
            generator,
            settings.llm_max_attempts,
            claim_check=settings.claim_check,
            claim_repair=settings.claim_repair,
            sufficiency_check=settings.sufficiency_check == "llm",
            rewrite_mode=settings.query_rewrite,
            rewriter=rewriter,
            rewrite_gap_score=settings.rewrite_gap_score,
        )
        self.research_retriever = ResearchRetriever(
            embedder=embedder, library=retriever, arxiv=arxiv, arxiv_enabled=settings.arxiv_enabled,
            arxiv_budget_s=settings.arxiv_budget_s, min_score=settings.min_score_for(embedder.backend),
            hybrid_bm25_enabled=settings.hybrid_bm25_enabled,
            reranker=reranker, rerank_candidates=settings.rerank_candidates,
            library_boost=settings.library_boost, rerank_library_boost=settings.rerank_library_boost,
            min_score_arxiv=settings.min_score_arxiv,
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
        return self._response(state)

    @staticmethod
    def _response(state: dict) -> tuple[dict, dict]:
        if state.get("outcome") == "error":
            if state.get("error") == "retrieval_failed":
                raise HTTPException(503, "Retrieval is temporarily unavailable. Please try again.")
            raise HTTPException(422, state.get("error") or "Invalid request.")
        sources = state.get("sources", [])
        report = state["citation_report"]
        response = {
            "question": state.get("followup_question") or state["question"],
            "standalone_question": state["question"] if state.get("followup_question") else None,
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
            "query_rewrites": state.get("query_rewrites", []),
        }
        timings = {
            "retrieval": state.get("retrieval_timings", {}),
            "generate_ms": sum(t["duration_ms"] for t in state["trace"] if t["stage"] in _GEN_STAGES),
        }
        return response, timings

    # ---- public API ----------------------------------------------------------
    @staticmethod
    def _ask_timings(t: dict, t0: float) -> dict:
        out = {"local": t["retrieval"].get("local", 0), "arxiv": t["retrieval"].get("arxiv", 0)}
        if "rerank" in t["retrieval"]:
            out["rerank"] = t["retrieval"]["rerank"]
        return out | {"generate": t["generate_ms"], "total": round((time.perf_counter() - t0) * 1000)}

    def _ask_key(self, question: str, top_k: int, use_arxiv: bool, history: list[dict]) -> tuple[str, bool, tuple]:
        question = " ".join(question.split())
        use_arxiv = use_arxiv and self.s.arxiv_enabled
        turns = tuple((t["question"].lower(), t.get("answer", "")) for t in history)  # hashed in core.cache
        return question, use_arxiv, ("ask", question.lower(), top_k, use_arxiv, turns)

    async def ask(self, question: str, top_k: int, use_arxiv: bool, history: list[dict] | None = None) -> dict:
        history = history or []
        question, use_arxiv, key = self._ask_key(question, top_k, use_arxiv, history)
        if hit := await self._cached(key):
            return hit

        t0 = time.perf_counter()
        state = await run_research(
            self.graph, self.research_retriever, question, top_k, {"use_arxiv": use_arxiv}, NO_SOURCES_MSG, history
        )
        response, t = self._response(state)
        response["timings_ms"] = self._ask_timings(t, t0)
        await self._store(key, response)
        return response

    async def ask_stream(self, question: str, top_k: int, use_arxiv: bool, history: list[dict] | None = None):
        """Same answer as :meth:`ask`, as events: ``stage``, ``sources``, ``token``, ``reset``, then
        ``done`` with the full response (identical shape to ``/ask``). A cache hit is a lone ``done``.
        Errors raise ``HTTPException`` like :meth:`ask`; the route turns them into an ``error`` event."""
        history = history or []
        question, use_arxiv, key = self._ask_key(question, top_k, use_arxiv, history)
        if hit := await self._cached(key):
            yield "done", hit
            return
        t0 = time.perf_counter()
        async for kind, payload in stream_research(
            self.graph, self.research_retriever, question, top_k, {"use_arxiv": use_arxiv}, NO_SOURCES_MSG, history
        ):
            if kind == "sources":
                yield kind, [_source_out(s, i) for i, s in enumerate(payload, 1)]
            elif kind != "final":
                yield kind, payload
            else:
                response, t = self._response(payload)
                response["timings_ms"] = self._ask_timings(t, t0)
                await self._store(key, response)
                yield "done", response

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
