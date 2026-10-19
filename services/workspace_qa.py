"""Grounded Q&A over one workspace's ingested documents, on the same LangGraph workflow and
citation machinery as /ask (P2-FR-06) - outcome, citations, claims, citation_check and the
workflow trace all come from the same graph, not a bespoke path. Reuses the compiled graph
built once in services.search_service.SearchService; the retriever is request-scoped (it
needs the caller's DB connection and access-group filter).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional

from core.sources import normalize_source
from services.research_graph import run_research
from services.retrieval import hybrid_search


@dataclass
class _RetrievalResult:
    sources: list[dict]
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)


class WorkspaceChunkRetriever:
    """Duck-typed to match what services.research_graph's `retrieve` node expects
    (`.aretrieve(question, top_k, **options)` -> an object with `.sources`/`.warnings`/
    `.timings_ms`) - deliberately NOT a `langchain_core.BaseRetriever` subclass: `core` ->
    `services` stays one-directional (ADR-001 dependency rule), and nothing here needs
    LangChain's own interop surface (no `ainvoke()` caller for workspace retrieval)."""

    def __init__(self, conn: Any, embedder: Any, workspace_id: str, allowed_group_ids: Optional[List[str]]):
        self.conn = conn
        self.embedder = embedder
        self.workspace_id = workspace_id
        self.allowed_group_ids = allowed_group_ids

    async def aretrieve(self, question: str, top_k: int, **_: Any) -> _RetrievalResult:
        qvec = (await self.embedder.embed([question]))[0]
        hits = await asyncio.to_thread(
            hybrid_search, self.conn, self.workspace_id, question, qvec, top_k, self.allowed_group_ids
        )
        sources = [
            normalize_source(
                {
                    "id": str(h["id"]),
                    "document_id": str(h["document_id"]),
                    "chunk_id": f"{h['document_id']}#{h['chunk_index']}",
                    "title": h["filename"],
                    "source": "local",
                    "url": "",
                    "text": h["content"],
                    "score": h["score"],
                    "page": h.get("page_number"),
                    "section_heading": h.get("section_heading"),
                }
            )
            for h in hits
        ]
        return _RetrievalResult(sources=sources)


async def ask_workspace(
    conn: Any,
    graph: Any,
    embedder: Any,
    workspace_id: str,
    question: str,
    top_k: int = 5,
    allowed_group_ids: Optional[List[str]] = None,
) -> dict:
    question = " ".join(question.split())
    retriever = WorkspaceChunkRetriever(conn, embedder, workspace_id, allowed_group_ids)
    state = await run_research(
        graph, retriever, question, top_k,
        empty_message="The documents in this workspace don't contain information to answer that.",
    )  # fmt: skip
    report = state["citation_report"]
    sources = state.get("sources", [])
    citations_out = [
        {
            "document_id": s["document_id"],
            "filename": s["title"],
            "page_number": s.get("page"),
            "section_heading": s.get("section_heading"),
            "snippet": s["text"][:300],
            "score": round(float(s.get("score", 0.0)), 4),
        }
        for s in sources
    ]
    return {
        "question": question,
        "answer": state["answer"],
        "sources": citations_out,
        "provider": state.get("provider", "none"),
        "model_used": state.get("model", "none"),
        "llm_used": bool(state.get("llm_used")),
        "warnings": list(dict.fromkeys(state.get("warnings", []))),
        "outcome": state["outcome"],
        "citations": report.citations,
        "claims": report.claims,
        "citation_check": report.summary(),
        "workflow": state["trace"],
        "query_rewrites": state.get("query_rewrites", []),
    }
