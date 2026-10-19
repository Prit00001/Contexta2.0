# Contexta 2.0 — System Design (Phase 1)

Diagrams: [context](diagrams/system-context.md) · [components](diagrams/components.md) · [request sequence](diagrams/request-sequence.md) · [LangGraph workflow](diagrams/langgraph-workflow.md). Decision record: [ADR-001](../decisions/ADR-001-project-architecture.md).

## 1. System context

Contexta is one FastAPI service. Users reach it through the bundled single-page UI (`app/static/index.html`) or directly over HTTP. It calls out to:

- **LLM providers** — Groq, Gemini (OpenAI-compatible endpoint), OpenRouter, or any custom OpenAI-compatible server, tried in `LLM_PROVIDERS` order.
- **arXiv API** — live paper search (optional per request).
- **Hugging Face / GCS** — one-time embedding-model download by fastembed (baked into the Docker image).
- **Postgres + pgvector, Redis, Supabase Storage/Auth** — only for the workspace platform; all optional.

## 2. Components and responsibilities

| Layer | Module | Responsibility |
|---|---|---|
| HTTP | `app/main.py` | App factory, lifespan (build shared objects once), middleware, static UI |
| | `app/routes.py` | `/ask`, `/ask-pdf`, `/health`, `/ready`, per-IP rate limit |
| | `app/routes_workspace.py`, `app/routes_admin.py`, `app/deps.py` | Workspace platform (unchanged contract) |
| Schemas | `app/schemas.py` | Pydantic request/response models + OpenAPI examples |
| Config | `app/config.py` | Validated Pydantic `Settings` from environment; provider catalog |
| Observability | `app/observability.py` | Logging setup (text/JSON), request-ID middleware, `log_event()` |
| Workflow | `services/research_graph.py` | LangGraph `StateGraph`: validate → retrieve → generate → validate answer → finalize |
| Orchestration | `services/search_service.py` | Answer cache, PDF-embedding cache, runs the graph, shapes the response |
| Retrieval | `core/lc_retriever.py` | LangChain `BaseRetriever` adapters: `ResearchRetriever` (FAISS library + arXiv, merge/dedupe/hybrid rank) and `PdfChunkRetriever` |
| | `core/retriever.py`, `core/vector_store.py`, `core/embeddings.py` | Existing FAISS index, disk-cached embeddings, fastembed/hash embedder (unchanged) |
| | `services/arxiv_client.py` | Existing arXiv client (unchanged contract) |
| Sources | `core/sources.py` | Normalises and validates source metadata (`document_id`, `chunk_id`, safe URL); converts to/from LangChain `Document` |
| Generation | `core/generator.py` | Provider fallback chain with cooldowns + extractive fallback (existing behaviour) |
| | `core/llm.py` | LangChain `ChatOpenAI` factory per provider; maps SDK exceptions to `LLMError` with cooldowns |
| | `core/prompts.py` | `ChatPromptTemplate` with untrusted-content framing |
| Attribution | `core/citations.py` | Marker validation, claim splitting, citation report |
| Persistence | `core/db.py`, `core/storage.py`, `services/ingestion.py`, `services/worker.py` | Workspace platform; storage now path-safe |

**Dependency rule:** `app` → `services` → `core`. LangChain imports are confined to `core/llm.py`, `core/prompts.py`, `core/lc_retriever.py`, `core/sources.py`; LangGraph only to `services/research_graph.py`. Swapping either framework touches only those files.

## 3. Request lifecycle (`POST /ask`)

1. `RequestContextMiddleware` assigns `request_id` (or accepts a safe incoming `X-Request-ID`), times the request, logs one `http.request` event, sets the response header.
2. Pydantic validates `AskRequest` (422 on failure). The rate limiter checks the client IP (429).
3. `SearchService.ask` normalises whitespace and checks the answer cache.
4. On a miss it invokes the compiled graph with the `ResearchRetriever` in `config["configurable"]`.
5. The graph runs its nodes (§5), each appending a `{stage, status, duration_ms}` trace entry and logging a `workflow.stage` event.
6. `SearchService` maps the final state to `AskResponse` (backward-compatible fields + `outcome`, `citations`, `claims`, `citation_check`, `workflow`, `request_id`) and caches it if an LLM produced it.

## 4. Retrieval and generation data flow

```
question ──embed──▶ qvec ──FAISS top 2k──▶ library candidates ┐
        └─keywords─▶ arXiv query ──▶ papers ──embed──▶ cosine ┴▶ hybrid score = 0.8·cos + 0.2·keyword overlap
                                                               ▶ dedupe (arXiv ID or title) ▶ ≥ MIN_SCORE ▶ top_k
top_k sources ──normalise──▶ numbered [1..k] prompt ──LLM chain──▶ answer
answer ──citation check──▶ cleaned answer + citations + claims + report
```

- **Documents vs chunks:** library entries and arXiv abstracts are one chunk per document (`chunk_id = <document_id>#0`). PDF chunks get `chunk_id = <document_id>#<n>` where `document_id = pdf:<sha1-prefix>` of the extracted text, plus a page number.
- **Grounding:** titles, URLs, authors and dates in the response come only from retrieval results. The LLM only produces the answer text; markers that do not map to a retrieved source are removed and reported.

## 5. LangGraph workflow

State (`ResearchState`, a `TypedDict`): `question`, `top_k`, `options`, `sources`, `warnings`, `diagnostics`, `answer`, `provider`, `model`, `llm_used`, `attempts`, `citation_report`, `outcome`, `error`, `trace`, `timings_ms`.

| Node | Does | Next |
|---|---|---|
| `validate_request` | Re-checks question length/emptiness and `top_k` bounds (defence in depth for non-HTTP callers) | invalid → `finalize` (error) · ok → `retrieve` |
| `retrieve` | `retriever.aretrieve(...)` → normalised sources + warnings | none → `insufficient_evidence` · some → `generate` · retriever raised → `finalize` (error, HTTP 503) |
| `generate` | `Generator.generate` (provider chain), attempts += 1 | LLM answer → `validate_answer` · no LLM → `fallback` |
| `validate_answer` | `validate_citations` | usable → `finalize` · unusable and attempts < `LLM_MAX_ATTEMPTS` → `generate` (the retry prompt adds a "you cited nothing" instruction) · else → `fallback` |
| `fallback` | Extractive answer from top sources + citation check | `finalize` |
| `insufficient_evidence` | Fixed "sources don't cover this" answer, no LLM call | `finalize` |
| `finalize` | Sets `outcome` | END |

Retries are bounded twice: `LLM_MAX_ATTEMPTS` (≤ 3) and the graph recursion limit (25). No tools, no autonomous loops.

## 6. Model-provider integration

`ProviderSpec` (name, base URL, model, key, extra headers, extra body) is built from env vars by `app/config.py`. `core/llm.py` creates one `ChatOpenAI` per spec with `max_retries=0`, `timeout=LLM_TIMEOUT`, `temperature=0.2`, a 1024-token output cap (sent by `langchain-openai` as `max_completion_tokens`), provider-specific headers (OpenRouter `HTTP-Referer`/`X-Title`) and body fields (`reasoning_effort` for gpt-oss, via `extra_body`).

The OpenAI SDK under `ChatOpenAI` (v3) is built on `httpx2`, while the app shares one `httpx.AsyncClient`. `HttpxBridgeTransport` (an `httpx2` transport) forwards every SDK request through that shared client, so connection pooling, proxy settings and the User-Agent are shared, and tests drive providers with `httpx.MockTransport` — no network in the default suite. SDK exceptions map to `LLMError(message, cooldown)` with the same cooldown policy as the pre-LangChain implementation (SRS SR-11).

## 7. Configuration and dependency boundaries

All settings are fields on one frozen Pydantic model created once (`get_settings()`, `lru_cache`). Invalid values fail fast at startup with `ConfigError`. Secret fields (`api_key`, `database_url`, `supabase_*` keys, `redis_url`) are excluded from `repr`, are never logged and never appear in responses. External dependencies are injected: `create_app(http_transport=...)` replaces the network for every outbound call, the generator and retriever are passed into the graph, and the retriever is supplied per run via `config["configurable"]` — tests swap them without monkey-patching network code.

## 8. Error-handling strategy

| Failure | Handling | User sees |
|---|---|---|
| Invalid request | Pydantic → 422 | Validation detail |
| Rate limit | 429 + `Retry-After` | Message |
| arXiv down/slow/malformed | Caught in retriever, library-only | `warnings[]` note |
| LLM provider error | Cooldown + next provider | `diagnostics[]` entry |
| All LLMs fail | Extractive answer | `outcome=extractive_fallback` |
| Answer lacks valid citations | Regenerate once, then extractive | Possibly `extractive_fallback` |
| Nothing retrieved | No LLM call | `outcome=insufficient_evidence` |
| Unexpected exception | Global handler, logged with `request_id` | 500 generic message + `request_id` |

## 9. Security considerations

- Secrets only from env; `.env` git-ignored; nothing secret in `/health`/`/ready` or logs.
- Prompt injection: sources are wrapped in `<source>` delimiters and the system prompt states they are untrusted data; the model has no tools, and its output is only displayed (HTML-escaped by the UI) — never executed.
- Uploads: size limits, magic-byte checks, filename sanitisation, storage root confinement.
- Rate limiting per IP for `/ask`; Redis-backed for workspaces when configured.
- XML parsed with `defusedxml`.
- No authentication on `/ask` by product decision (public demo, A-01).

## 10. Decisions and trade-offs (summary)

| Decision | Why | Trade-off |
|---|---|---|
| Keep the existing provider chain; implement each link with LangChain `ChatOpenAI` | Preserves proven fallback/cooldown behaviour while standardising the client | Extra dependency (`openai` SDK) |
| Retrieval adapter as `BaseRetriever` with a richer `aretrieve()` | LangChain interop plus warnings/timings the standard interface can't carry | Two entry points to keep consistent |
| Linear graph with 4 conditional edges | Explicit, testable, matches the problem; no agent loop | Less "impressive", deliberately |
| In-memory FAISS, no Qdrant | 25 docs; exact search is instant | Re-evaluate in Phase 2 with real corpus size |
| No mass reformat of legacy files | Keeps diffs reviewable | Formatting check scoped to new/changed modules |

Full rationale: [ADR-001](../decisions/ADR-001-project-architecture.md).

## 11. Planned Phase 2 additions (Stage B — not yet built)

Per [PRD_PHASE_2.md](../requirements/PRD_PHASE_2.md); this section gets filled in with real design
detail as each lands in Stage C, not speculatively ahead of time.

| Addition | Where | Driven by |
|---|---|---|
| BM25 fused with the existing hybrid ranking (RRF) | new module alongside `core/lc_retriever.py`, flag-gated | P2-FR-03 |
| Claim-support check | new graph node after `validate_answer` in `services/research_graph.py` | P2-FR-04 |
| Workspace Q&A on the graph | `services/workspace_qa.py` calls the compiled graph instead of `Generator` directly | P2-FR-06 |
| Redis-backed `/ask` cache + limiter, `GET /metrics` | `app/main.py`, new `app/metrics.py` | P2-FR-07 |

Eval harness (`eval/run_eval.py`, `core/lc_retriever.ResearchRetriever` reused directly) and its
dataset (`eval/dataset/`) are Stage A, already built — see [ADR-002](../decisions/ADR-002-evaluation-methodology.md).
