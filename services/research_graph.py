"""The research workflow as an explicit LangGraph ``StateGraph`` (FR-15, SR-20..SR-24).

    validate_request -> retrieve -> generate -> validate_answer -> [check_claims] -> finalize
                         |            |              |  ^                ^
                         |            |              |  | retry with feedback (attempts < max)
                         |            v              v  |                |
                         |         fallback <--------+--+ ---------------+   (no LLM answer / still unusable)
                         v
                 insufficient_evidence -> finalize

* ``check_claims`` (P2-FR-04) exists only when ``claim_check=True`` (``CLAIM_CHECK=llm``).
* ``contextualize`` (P2-14, ``core.followup``) runs first when the request carries ``history``: it
  turns a follow-up into a standalone question, which every later node uses.
* ``rewrite`` (Phase 3, ``core.query_rewrite``) exists only when ``rewrite_mode != "off"``. In
  ``single`` / ``multi`` / ``decompose`` it runs before ``retrieve``; in ``selective`` it runs after a
  weak first retrieval and loops back to ``retrieve`` once (``rewritten`` bounds the loop).

* Conditional edges exist only where the next step genuinely depends on the result.
* Generation is bounded by ``LLM_MAX_ATTEMPTS`` (1-3) and the graph by a recursion limit.
* The retriever is injected per run via ``config["configurable"]["retriever"]``, so one compiled
  graph serves both the paper library (``/ask``) and uploaded PDFs (``/ask-pdf``).
* No tools and no autonomous planning: the model only ever writes answer text.
"""

from __future__ import annotations

import asyncio
import logging
import operator
import time
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from core import metrics
from core.abstention import is_abstention
from core.citations import validate_citations
from core.claim_support import check_claims as run_claim_check
from core.followup import MAX_TURNS, contextualize
from core.generator import Generator, extractive_answer
from core.prompts import RETRY_FEEDBACK
from core.query_rewrite import MODES as REWRITE_MODES
from core.query_rewrite import fuse, retrieval_queries, rewrite_question

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
    # follow-ups (P2-14): client-sent turns; ``question`` becomes the standalone question
    history: list[dict]
    followup_question: str  # what the user typed, when history was given
    # query rewriting (Phase 3)
    queries: list[str]  # what retrieve searches with, original first (absent = the question only)
    query_rewrites: list[str]  # the rewriter's queries, reported in the response
    rewritten: bool
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
            metrics.STAGE_SECONDS.labels(stage, status).observe(time.perf_counter() - t0)
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


def build_research_graph(
    generator: Generator,
    max_attempts: int = 2,
    claim_check: bool = False,
    rewrite_mode: str = "off",
    rewriter: Generator | None = None,
    rewrite_gap_score: float = 0.35,
):
    if rewrite_mode not in REWRITE_MODES:
        raise ValueError(f"unknown rewrite mode {rewrite_mode!r}")
    max_attempts = max(1, min(int(max_attempts), 3))
    rewriter = rewriter or generator  # REWRITE_PROVIDERS may point rewriting at another chain (e.g. Ollama)
    rewrite_first = rewrite_mode in ("single", "multi", "decompose")
    answered_next = "check_claims" if claim_check else "finalize"

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

    @_traced("contextualize")
    async def contextualize_followup(state: ResearchState, config: RunnableConfig):
        history = state["history"][-MAX_TURNS:]
        standalone, by_llm, diagnostics = await contextualize(state["question"], history, generator.complete)
        update = {"followup_question": state["question"], "question": standalone, "diagnostics": diagnostics}
        if not by_llm:
            update["_category"] = "llm_unavailable"
        return update, "ok" if by_llm else "fallback"

    @_traced("rewrite")
    async def rewrite(state: ResearchState, config: RunnableConfig):
        rewrites, diagnostics = await rewrite_question(state["question"], rewrite_mode, rewriter.complete)
        update = {
            "rewritten": True,
            "diagnostics": diagnostics,
            "query_rewrites": rewrites or [],
            "queries": retrieval_queries(state["question"], rewrites, rewrite_mode),
        }
        if rewrites is None:
            update["_category"] = "llm_unavailable"
            return update, "failed"
        return update, "ok"

    async def _retrieve_all(retriever, state: ResearchState):
        question, options = state["question"], state.get("options", {})
        queries = state.get("queries") or [question]
        if queries == [question]:
            return await retriever.aretrieve(question, state["top_k"], **options)

        # Rewrites search the local index only; live arXiv is queried for the original question
        # alone, so rewriting never multiplies calls to an external API.
        def opts(q: str) -> dict:
            return options if q == question else {**options, "use_arxiv": False}

        results = await asyncio.gather(*(retriever.aretrieve(q, state["top_k"], **opts(q)) for q in queries))
        timings: dict[str, int] = {}
        for r in results:
            for k, v in r.timings_ms.items():
                timings[k] = timings.get(k, 0) + v
        result = results[0]
        result.sources = fuse([r.sources for r in results], state["top_k"])
        result.warnings = list(dict.fromkeys(w for r in results for w in r.warnings))
        result.timings_ms = timings
        return result

    @_traced("retrieve")
    async def retrieve(state: ResearchState, config: RunnableConfig):
        retriever = config["configurable"]["retriever"]
        try:
            result = await _retrieve_all(retriever, state)
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
        if is_abstention(report.answer, report.valid_markers):
            # The model followed prompt rule 2: nothing retrieved answers the question. Keep its
            # explanation instead of retrying or quoting irrelevant sources (core.abstention).
            update = {"citation_report": report, "answer": report.answer, "outcome": "insufficient_evidence"}
            return update | {"_category": "insufficient_evidence"}, "abstained"
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

    @_traced("check_claims")
    async def check_claims(state: ResearchState, config: RunnableConfig):
        report = state["citation_report"]
        status, diagnostics = await run_claim_check(report, state["sources"], generator.complete)
        update = {"citation_report": report, "diagnostics": diagnostics}
        if status != "ok":
            update["_category"] = "llm_unavailable" if status == "judge_unavailable" else "llm_invalid_output"
        return update, status

    @_traced("finalize")
    async def finalize(state: ResearchState, config: RunnableConfig):
        outcome = state.get("outcome") or "answered"
        return {"outcome": outcome}, outcome

    def after_validate_request(state: ResearchState) -> str:
        if state.get("outcome") == "error":
            return "finalize"
        if state.get("history") and not state.get("followup_question"):
            return "contextualize"
        return "rewrite" if rewrite_first else "retrieve"

    def retrieval_is_weak(state: ResearchState) -> bool:
        sources = state.get("sources") or []
        return not sources or max(s["score"] for s in sources) < rewrite_gap_score

    def after_retrieve(state: ResearchState) -> str:
        if state.get("outcome") == "error":
            return "finalize"
        if rewrite_mode == "selective" and not state.get("rewritten") and retrieval_is_weak(state):
            return "rewrite"
        return "generate" if state.get("sources") else "insufficient_evidence"

    def after_generate(state: ResearchState) -> str:
        return "fallback" if state.get("fallback_answer") is not None else "validate_answer"

    def after_validate_answer(state: ResearchState) -> str:
        report = state["citation_report"]
        if report.usable(has_sources=bool(state["sources"])):
            return answered_next
        if state.get("outcome") == "insufficient_evidence":  # LLM abstained (validate_answer)
            return "finalize"
        return "generate" if state["attempts"] < max_attempts else "fallback"

    g = StateGraph(ResearchState)
    nodes = [validate_request, retrieve, generate, validate_answer, fallback, insufficient_evidence, finalize]
    nodes.append(contextualize_followup)
    if claim_check:
        nodes.append(check_claims)
    if rewrite_mode != "off":
        nodes.append(rewrite)
    for node in nodes:
        g.add_node(node.__name__, node)
    g.add_edge(START, "validate_request")
    first_search = "rewrite" if rewrite_first else "retrieve"
    g.add_conditional_edges("validate_request", after_validate_request, [first_search, "contextualize", "finalize"])
    g.add_edge("contextualize", first_search)
    after_retrieve_targets = ["generate", "insufficient_evidence", "finalize"]
    if rewrite_mode == "selective":
        after_retrieve_targets.append("rewrite")
    g.add_conditional_edges("retrieve", after_retrieve, after_retrieve_targets)
    if rewrite_mode != "off":
        g.add_edge("rewrite", "retrieve")
    g.add_conditional_edges("generate", after_generate, ["validate_answer", "fallback"])
    g.add_conditional_edges(
        "validate_answer",
        after_validate_answer,
        list(dict.fromkeys([answered_next, "finalize", "generate", "fallback"])),
    )
    g.add_edge("fallback", answered_next)
    if claim_check:
        g.add_edge("check_claims", "finalize")
    g.add_edge("insufficient_evidence", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def _initial_state(
    question: str, top_k: int, options: dict | None, empty_message: str | None, history: list[dict] | None = None
) -> ResearchState:
    state: ResearchState = {
        "history": list(history or []),
        "question": question,
        "top_k": top_k,
        "options": options or {},
        "warnings": [],
        "diagnostics": [],
        "trace": [],
    }
    if empty_message:
        state["empty_message"] = empty_message
    return state


def _config(retriever) -> dict:
    return {"configurable": {"retriever": retriever}, "recursion_limit": RECURSION_LIMIT}


async def run_research(
    graph,
    retriever,
    question: str,
    top_k: int,
    options: dict | None = None,
    empty_message: str | None = None,
    history: list[dict] | None = None,
) -> ResearchState:
    state = _initial_state(question, top_k, options, empty_message, history)
    return await graph.ainvoke(state, config=_config(retriever))


async def stream_research(
    graph,
    retriever,
    question: str,
    top_k: int,
    options: dict | None = None,
    empty_message: str | None = None,
    history: list[dict] | None = None,
):
    """Run the workflow, yielding events as it goes (``POST /ask/stream``):

    * ``("stage", {"stage", "status", "duration_ms"})`` after every node;
    * ``("sources", [source, ...])`` once retrieval has results;
    * ``("token", text)`` - answer text from the ``generate`` node only (rewrite and claim-check
      calls also stream internally, but are never shown);
    * ``("reset", {"attempt"})`` - the streamed draft failed citation validation and is being
      regenerated, so clients must discard the text shown so far;
    * ``("final", state)`` - the complete final state, last.

    Streamed tokens are the model's raw draft: hallucinated ``[n]`` markers are only removed in the
    final state, which is what clients must keep.
    """
    final: dict = {}
    generated = 0
    async for mode, payload in graph.astream(
        _initial_state(question, top_k, options, empty_message, history),
        config=_config(retriever),
        stream_mode=["messages", "updates", "values"],
    ):
        if mode == "messages":
            chunk, meta = payload
            text = chunk.content if isinstance(chunk.content, str) else ""
            if meta.get("langgraph_node") == "generate" and text:
                yield "token", text
        elif mode == "updates":
            for node, update in payload.items():
                for step in (update or {}).get("trace", []):
                    yield "stage", step
                    generated += node == "generate"
                    if node == "validate_answer" and step["status"] == "retry":
                        yield "reset", {"attempt": generated + 1}
                if node == "retrieve" and (update or {}).get("sources"):
                    yield "sources", update["sources"]
        else:
            final = payload
    yield "final", final
