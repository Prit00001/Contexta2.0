# ADR-001: Phase 1 architecture — evolve the monolith, add LangChain/LangGraph behind adapters

| | |
|---|---|
| Status | Accepted |
| Date | 2026-10-16 |
| Scope | Contexta 2.0, Phase 1 |

## Context

The baseline (`3f59589`) is a working FastAPI monolith: a public research endpoint (`/ask`) over a FAISS-indexed 25-paper library plus live arXiv; a PDF Q&A demo (`/ask-pdf`); and a multi-tenant workspace platform (Supabase auth, pgvector, ingestion worker). Its strengths are graceful degradation (provider fallback chain, extractive answers, hash-embedding fallback) and a no-network test suite (25 tests passing). Its gaps are listed in the Phase 1 audit: no workflow structure, no LangChain/LangGraph, weak citation validation, no readiness, unvalidated config, unstructured logs, and two security defects.

Phase 1 must integrate LangChain and LangGraph meaningfully, keep what works, and stay small enough to explain in an interview.

## Decision

1. **Keep the single-service architecture and the `app → services → core` layering.** Add modules; do not reorganise packages.
2. **LangChain behind four adapter modules** (`core/llm.py`, `core/prompts.py`, `core/lc_retriever.py`, `core/sources.py`). The rest of the code uses project types (plain source dicts, `GenResult`, `CitationReport`).
3. **LLM access:** each provider in the existing chain becomes a `langchain_openai.ChatOpenAI` instance. The chain, cooldowns and extractive fallback in `core/generator.py` stay — LangChain's `with_fallbacks` has no cooldown memory or error-specific back-off, which the free-tier deployment relies on.
4. **Retrieval:** the existing FAISS retriever, arXiv client and hybrid ranking move into a `BaseRetriever` subclass (`ResearchRetriever`) with an additional `aretrieve()` that also returns warnings and timings. A sibling `PdfChunkRetriever` serves `/ask-pdf`.
5. **Workflow:** one small LangGraph `StateGraph` (validate → retrieve → generate → validate answer → finalize, with insufficient-evidence and fallback paths and a bounded regeneration loop). Retriever injected per run through `config["configurable"]`, so the same compiled graph serves `/ask` and `/ask-pdf`.
6. **Configuration:** replace the dataclass with a frozen Pydantic model, same field names, strict parsing.
7. **Workspace platform:** preserved as-is except for security fixes. It keeps calling `Generator` directly; moving it onto the graph is deferred.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Rewrite around a LangChain LCEL chain (`prompt \| llm \| parser`) end-to-end | Would discard the proven fallback/cooldown/extractive logic and couple every module to LangChain |
| LangChain `with_fallbacks()` for the provider chain | No cooldown state; a rate-limited provider would be retried on every request, burning latency and quota |
| Agentic LangGraph (ReAct, tool-calling, planner) | Not needed for single-hop QA; harder to test and bound; brief forbids agents for show |
| Qdrant / pgvector for the public library | 25 documents; exact in-memory FAISS is faster and simpler. Revisit in Phase 2 with measurements |
| `pydantic-settings` | Plain Pydantic validation covers our needs without another dependency |
| Split into microservices (API / retrieval / generation) | Premature; one process fits the 512 MB free tier |

## Consequences

**Positive:** explicit, unit-testable workflow; framework code isolated and replaceable; existing behaviour and tests preserved; clearer response contract (outcome, citations, claims).

**Negative / trade-offs:**
- New dependencies: `langchain-core`, `langgraph`, `langchain-openai` (+ `openai`, `tiktoken` transitively) — more to pin and update.
- Two retrieval entry points (`aretrieve` and `ainvoke`) must stay consistent; a test covers both.
- The workspace path doesn't use the graph yet, so it lacks claim-level citation reports.
- Citation validation is marker-level only; entailment checking is a Phase 2 item.
