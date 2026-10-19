# Contexta 2.0 — Phase 1 Implementation Plan

Status legend: ☐ planned · ◐ in progress · ☑ done (acceptance criteria met and tests executed) · ⚠ done with an unverified part (see notes).

Baseline before any change: 25 tests passed (22 unit + 3 DB integration against Postgres 16 + pgvector 0.8.0), `ruff check` clean — see [DEVELOPMENT_LOG](DEVELOPMENT_LOG.md#session-1).

| Task | Req IDs | Files / components | Approach | Depends on | Acceptance criteria | Tests | Status |
|---|---|---|---|---|---|---|---|
| T-00 Requirements & design docs | all | `docs/requirements/*`, `docs/architecture/*`, ADR-001, this plan | Audit-driven docs before code | — | Docs exist, IDs consistent | Review | ☑ |
| T-01 Sanitise upload filenames; confine local storage | SR-30, NFR-04 | `core/storage.py`, `app/routes_workspace.py` | `safe_filename()`; `LocalStorage` resolves and rejects paths outside root | — | `../../x.md` stored as `x.md`; direct traversal raises | `tests/test_storage.py` | ☑ |
| T-02 JWT secret guard | SR-31 | `app/deps.py` | 503 when secret unset, before decode; generic 401 text | — | Forged/any token with no secret → 503 | `tests/test_auth.py` | ☑ |
| T-03 Validated Pydantic settings | FR-08, SR-02–04 | `app/config.py`, `.env.example` | Frozen `BaseModel`, strict env parsing, `REQUIRE_LLM`, `LLM_MAX_ATTEMPTS`, `LOG_*`, `TRUST_PROXY_HEADERS`; key not in repr | — | Bad env → `ConfigError` naming var; existing callers unchanged | `tests/test_config.py` | ☑ |
| T-04 Trusted proxy IP | SR-36 | `app/routes.py` | Right-most XFF entry when trusted | T-03 | Spoofed left entry doesn't change bucket | `tests/test_api.py` | ☑ |
| T-05 Readiness endpoint | FR-02 | `app/routes.py`, `app/schemas.py`, `core/embeddings.py` | `/ready` with checks; embedder exposes `requested_backend` | T-03 | 200 ready/degraded, 503 not_ready; no secrets | `tests/test_health.py` | ☑ |
| T-06 Source normalisation | FR-04, FR-06, SR-35 | `core/sources.py` | `normalize_source()`, `safe_url()`, `to_document()/from_document()` | — | Every source has `document_id`/`chunk_id`; bad URL → "" | `tests/test_sources.py` | ☑ |
| T-07 Citation validator | FR-05, FR-06 | `core/citations.py` | Parse `[n]`, drop invalid, claims with status | T-06 | Report counts correct on fixtures | `tests/test_citations.py` | ☑ |
| T-08 Prompt template | SR-32, FR-14 | `core/prompts.py` | `ChatPromptTemplate`; `<source>` delimiters; untrusted framing | — | Injection text stays inside delimiters; system prompt states rule | `tests/test_prompts.py` | ☑ |
| T-09 LangChain LLM adapter | FR-14, SR-10, SR-11 | `core/llm.py`, `core/generator.py` | `ChatOpenAI` per spec, injected httpx client, `max_retries=0`; exception → `LLMError` | T-08 | Existing generator tests pass unchanged; new error-mapping tests pass | `tests/test_generator.py`, `tests/test_llm.py` | ☑ |
| T-10 Retriever adapters | FR-04, FR-07, FR-14 | `core/lc_retriever.py` | Move ranking from `SearchService` into `ResearchRetriever`/`PdfChunkRetriever` | T-06 | Same ranking as baseline; arXiv failure modes → warning | `tests/test_lc_retriever.py` | ☑ |
| T-11 LangGraph workflow | FR-15, SR-20–24 | `services/research_graph.py` | `StateGraph` per design; trace per node | T-07, T-09, T-10 | Each routing edge covered; attempts bounded | `tests/test_research_graph.py` | ☑ |
| T-12 Wire graph into `/ask`, `/ask-pdf` | FR-03, FR-11, FR-12 | `services/search_service.py`, `app/schemas.py` | Service runs graph, response additive | T-11 | Baseline API tests pass; new fields present | `tests/test_api.py` | ☑ |
| T-13 Observability | FR-10, NFR-08 | `app/observability.py`, `app/main.py` | Text/JSON formatter, request-ID middleware, stage events, 500 handler | T-03 | JSON lines with request_id/duration; no question text or key | `tests/test_observability.py` | ☑ |
| T-14 OpenAPI examples | FR-09 | `app/schemas.py`, `app/routes.py` | `json_schema_extra` examples, `responses=` | T-12 | Examples present in `/openapi.json` | `tests/test_api.py` | ☑ |
| T-15 Integration + E2E smoke | §8 of brief | `tests/integration/test_research_flow.py`, `scripts/smoke_test.py` | App with mocked arXiv/LLM via `httpx.MockTransport`; smoke script against running server | T-12 | All failure scenarios covered; smoke passes on live server | pytest + script run | ☑ |
| T-16 Tooling: lockfile, ruff, Docker, CI | NFR-06, NFR-07 | `requirements.in/txt`, `pyproject.toml`, `Dockerfile`, `.github/workflows/ci.yml`, `Makefile` | `uv pip compile`; ruff config; CI with pgvector service | T-09 | Fresh venv from lockfile passes tests | Fresh-venv run | ⚠ lockfile/ruff verified on 3.11 + 3.13; Docker build and CI not executed (registry blocked, no remote) |
| T-17 Documentation set & reports | §10–11 of brief | README, `docs/**`, CHANGELOG | Written against final code | all | Docs consistent with code; completion report with RTM | Review | ☑ |
| T-18 Phase 3 research brief | §12 | `docs/research/PHASE_3_QUERY_REWRITING_RESEARCH.md` | Verify papers via search; separate claims from hypotheses | — | Each paper's venue verified or flagged | Review | ☑ |

## Notes on deviations from the plan

| Task | Deviation | Reason |
|---|---|---|
| T-09 | Added `HttpxBridgeTransport` | `langchain-openai` → OpenAI SDK v3 uses `httpx2`; bridging keeps one shared client and `MockTransport` tests |
| T-09 | Generator test fixture gained `role: "assistant"` | Spec-conformant; LangChain rejects messages without a role |
| T-11 | Retry sends feedback ("you cited nothing") | A retry with the identical prompt at temperature 0.2 would mostly repeat the same answer |
| T-13 | 500 handling lives in the request middleware | Starlette runs `Exception` handlers outside user middleware, so the 500 would lack `X-Request-ID` |
| T-15 | Added `create_app(http_transport=...)` and an E2E stub LLM | Needed for network-free integration and real-socket E2E tests |
| — | Added ONNX telemetry opt-out | Found during manual smoke runs (connections to `mobile.events.data.microsoft.com`) |
