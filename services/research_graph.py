"""The research workflow as an explicit LangGraph ``StateGraph`` (FR-15, SR-20..SR-24).

    validate_request -> retrieve -> generate -> validate_answer -> finalize
                         |            |              |  ^
                         |            |              |  | retry with feedback (attempts < max)
                         |            v              v  |
                         |         fallback <--------+--+   (no LLM answer / still unusable)
                         v
                 insufficient_evidence -> finalize

* Conditional edges exist only where the next step genuinely depends on the result.
* Generation is bounded by ``LLM_MAX_ATTEMPTS`` (1-3) and the graph by a recursion limit.
* The retriever is injected per run via ``config["configurable"]["retriever"]``, so one compiled
  graph serves both the paper library (``/ask``) and uploaded PDFs (``/ask-pdf``).
* No tools and no autonomous planning: the model only ever writes answer text.
"""

from __future__ import annotations

import logging
import operator
import time
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from core.citations import validate_citations
from core.generator import Generator, extractive_answer
from core.prompts import RETRY_FEEDBACK

log = logging.getLogger("contexta.workflow")

MAX_QUESTION_CHARS = 1000
RECURSION_LIMIT = 25

NO_PROVIDER = "No AI provider configured; answer assembled from top sources."
ALL_FAILED = "All AI providers failed; answer assembled from top sources."

Outcome = Literal["answered", "extractive_fallback", "insufficient_evidence", "error"]


class ResearchState(TypedDict, total=False):
    # inputs
    question: str
    top_k: int
    options: dict  # retriever options, e.g. {"use_arxiv": True}
    empty_message: str  # what to say when nothing relevant is retrieved
    # retrieval
    sources: list[dict]
    retrieval_timings: dict
    # generation
    answer: str
    provider: str
    model: str
    llm_used: bool
    attempts: int
    removed_markers: list[int]
    fallback_answer: Optional[str]
    citation_report: Any  # core.citations.CitationReport
    # outcome
    outcome: Outcome
    error: Optional[str]
    # append-only channels
    warnings: Annotated[list[str], operator.add]
    diagnostics: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]


def _traced(stage: str):
    """Time a node, add a trace entry and log one structured event per stage (FR-10)."""

    def wrap(fn):
        async def node(state: ResearchState, config: RunnableConfig) -> dict:
            t0 = time.perf_counter()
            update, status = await fn(state, config)
            ms = round((time.perf_counter() - t0) * 1000)
            category = update.pop("_category", None)
            log.info(
                "stage %s %s",
                stage,
                status,
                extra={
                    "event": "workflow.stage",
                    "stage": stage,
                    "status": status,
                    "duration_ms": ms,
                    "category": category,
                },
            )
            update["trace"] = [{"stage": stage, "status": status, "duration_ms": ms}]
            return update

        node.__name__ = stage
        return node

    return wrap


def build_research_graph(generator: Generator, max_attempts: int = 2):
    max_attempts = max(1, min(int(max_attempts), 3))

    @_traced("validate_request")
    async def validate_request(state: ResearchState, config: RunnableConfig):
        question = " ".join(str(state.get("question", "")).split())
        top_k = state.get("top_k", 5)
        if not 3 <= len(question) <= MAX_QUESTION_CHARS:
            return {
                "question": question,
                "outcome": "error",
                "error": "question must be 3-1000 characters",
                "_category": "validation",
            }, "invalid"
        if not isinstance(top_k, int) or not 1 <= top_k <= 10:
            return {
                "question": question,
                "outcome": "error",
                "error": "top_k must be between 1 and 10",
                "_category": "validation",
            }, "invalid"
        return {"question": question, "attempts": 0, "removed_markers": []}, "ok"

    @_traced("retrieve")
    async def retrieve(state: ResearchState, config: RunnableConfig):
        retriever = config["configurable"]["retriever"]
        try:
            result = await retriever.aretrieve(state["question"], state["top_k"], **state.get("options", {}))
        except Exception as exc:  # noqa: BLE001 - explicit error state instead of an unhandled 500
            log.exception("retrieval failed")
            return {
                "outcome": "error",
                "error": "retrieval_failed",
                "sources": [],
                "diagnostics": [f"retrieval failed ({type(exc).__name__})"],
                "_category": "retrieval_unavailable",
            }, "error"
        status = "ok" if result.sources else "empty"
        return {
            "sources": result.sources,
            "warnings": result.warnings,
            "retrieval_timings": result.timings_ms,
            "_category": None if result.sources else "insufficient_evidence",
        }, status

    @_traced("generate")
    async def generate(state: ResearchState, config: RunnableConfig):
        attempts = state.get("attempts", 0) + 1
        feedback = RETRY_FEEDBACK if attempts > 1 else None
        gen = await generator.generate(state["question"], state["sources"], feedback=feedback)
        update = {"attempts": attempts, "diagnostics": gen.warnings}
        if not gen.llm_used:
            update["fallback_answer"] = gen.answer
            update["_category"] = "llm_unavailable"
            return update, "no_llm"
        update.update(
            answer=gen.answer,
            provider=gen.provider,
            model=gen.model,
            llm_used=True,
            removed_markers=gen.removed_markers,
        )
        return update, "ok"

    @_traced("validate_answer")
    async def validate_answer(state: ResearchState, config: RunnableConfig):
        report = validate_citations(state["answer"], state["sources"], state.get("removed_markers"))
        if report.usable(has_sources=bool(state["sources"])):
            return {"citation_report": report, "answer": report.answer}, "ok"
        status = "retry" if state["attempts"] < max_attempts else "unusable"
        return {"citation_report": report, "_category": "llm_invalid_output"}, status

    @_traced("fallback")
    async def fallback(state: ResearchState, config: RunnableConfig):
        if state.get("fallback_answer") is not None:  # no provider produced an answer
            note = ALL_FAILED if generator.providers else NO_PROVIDER
            answer = state["fallback_answer"]
        else:  # an LLM answered, but without valid citations on every attempt
            n = state.get("attempts", 0)
            note = f"AI answer had no valid citations after {n} attempt(s); answer assembled from top sources."
            answer = extractive_answer(state["question"], state["sources"])
        report = validate_citations(answer, state["sources"])
        return {
            "answer": report.answer,
            "citation_report": report,
            "provider": "none",
            "model": "extractive",
            "llm_used": False,
            "outcome": "extractive_fallback",
            "diagnostics": [note],
        }, "ok"

    @_traced("insufficient_evidence")
    async def insufficient_evidence(state: ResearchState, config: RunnableConfig):
        answer = state.get("empty_message") or "The available sources don't contain enough information to answer."
        return {
            "answer": answer,
            "citation_report": validate_citations(answer, []),
            "provider": "none",
            "model": "none",
            "llm_used": False,
            "outcome": "insufficient_evidence",
        }, "ok"

    @_traced("finalize")
    async def finalize(state: ResearchState, config: RunnableConfig):
        outcome = state.get("outcome") or "answered"
        return {"outcome": outcome}, outcome

    def after_validate_request(state: ResearchState) -> str:
        return "finalize" if state.get("outcome") == "error" else "retrieve"

    def after_retrieve(state: ResearchState) -> str:
        if state.get("outcome") == "error":
            return "finalize"
        return "generate" if state.get("sources") else "insufficient_evidence"

    def after_generate(state: ResearchState) -> str:
        return "fallback" if state.get("fallback_answer") is not None else "validate_answer"

    def after_validate_answer(state: ResearchState) -> str:
        report = state["citation_report"]
        if report.usable(has_sources=bool(state["sources"])):
            return "finalize"
        return "generate" if state["attempts"] < max_attempts else "fallback"

    g = StateGraph(ResearchState)
    for node in (validate_request, retrieve, generate, validate_answer, fallback, insufficient_evidence, finalize):
        g.add_node(node.__name__, node)
    g.add_edge(START, "validate_request")
    g.add_conditional_edges("validate_request", after_validate_request, ["retrieve", "finalize"])
    g.add_conditional_edges("retrieve", after_retrieve, ["generate", "insufficient_evidence", "finalize"])
    g.add_conditional_edges("generate", after_generate, ["validate_answer", "fallback"])
    g.add_conditional_edges("validate_answer", after_validate_answer, ["finalize", "generate", "fallback"])
    g.add_edge("fallback", "finalize")
    g.add_edge("insufficient_evidence", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


async def run_research(
    graph, retriever, question: str, top_k: int, options: dict | None = None, empty_message: str | None = None
) -> ResearchState:
    state: ResearchState = {
        "question": question,
        "top_k": top_k,
        "options": options or {},
        "warnings": [],
        "diagnostics": [],
        "trace": [],
    }
    if empty_message:
        state["empty_message"] = empty_message
    return await graph.ainvoke(
        state, config={"configurable": {"retriever": retriever}, "recursion_limit": RECURSION_LIMIT}
    )
