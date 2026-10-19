# Test Strategy (Phase 1)

## Principles
1. **No network, no paid calls in the default suite.** Every outbound call (arXiv, LLM providers) goes through an injected `httpx` transport; tests use `httpx.MockTransport`. The embedding model is replaced by deterministic hash embeddings (`EMBED_BACKEND=hash`, set in `tests/conftest.py`).
2. **Real components wherever affordable.** Integration tests run the real FastAPI app, real FAISS index over the real 25-paper library, real LangGraph workflow, real `ChatOpenAI` + OpenAI SDK (only the socket is mocked). DB tests use real Postgres + pgvector.
3. **Failure paths are first-class.** Each external dependency has tests for its failure modes, not just the happy path.
4. **Never weaken a test to pass.** One existing fixture was changed in Phase 1 (mock chat completion gained `role: "assistant"`, which real providers always send and LangChain requires); the change is recorded in the development log.

## Test levels

| Level | Location | What runs for real | What is replaced |
|---|---|---|---|
| Unit | `tests/test_*.py` | One module or function | Collaborators via fakes (e.g. `FakeGenerator`, `FakeRetriever`) |
| Integration — research API | `tests/integration/test_research_flow.py` | App, FAISS, graph, LangChain/OpenAI SDK, validation, logging | Network (`MockTransport` routing by host) |
| Integration — workspace platform | `tests/integration/test_workspace_api.py`, `test_tenant_isolation.py`, `test_ingestion_pipeline.py` | Postgres 16 + pgvector, RLS, auth, ingestion | Supabase Auth (JWT minted with a test secret); skipped unless `DATABASE_URL` is set |
| End-to-end smoke | `tests/e2e/test_smoke_e2e.py` + `scripts/smoke_test.py` | Two real processes over TCP: uvicorn serving Contexta, and an OpenAI-compatible stub LLM | Only the LLM's intelligence (deterministic stub); no arXiv |
| Manual smoke | `scripts/smoke_test.py --base-url …` | Whatever is configured, incl. real keys | Nothing |

## Requirement coverage

| Requirement | Tests |
|---|---|
| FR-01 startup | e2e (uvicorn subprocess); manual runs in TEST_REPORT |
| FR-02 health/readiness | `test_health.py` (ready/degraded/not_ready, embedding fallback, no secrets), integration (provider cooldown reported) |
| FR-03 query API | `test_api.py`, integration happy path, validation cases, OpenAPI test |
| FR-04 retrieval + IDs | `test_lc_retriever.py`, `test_sources.py`, integration (prompt numbering = response markers) |
| FR-05 grounded generation | `test_research_graph.py` (insufficient evidence skips LLM, retries, fallback), integration (hallucinated markers) |
| FR-06 attribution | `test_citations.py`, `test_sources.py` (URL validation), integration (malformed metadata) |
| FR-07 arXiv | `test_arxiv.py`, `test_lc_retriever.py` + integration: HTTP 503, malformed XML, connect error, timeout, budget exceeded, empty feed, disabled |
| FR-08 config | `test_config.py` (invalid values, REQUIRE_LLM, repr), `test_llm.py::test_api_key_never_logged`, integration REQUIRE_LLM startup |
| FR-09 OpenAPI | `test_observability.py::test_openapi_has_examples_and_error_responses` |
| FR-10 observability | `test_observability.py` (JSON events, request IDs, categories, redaction, no question text) |
| FR-11 UI | `test_api.py::test_health_and_index` (serves `/`); response shape backward compatible (baseline tests unchanged) |
| FR-12 PDF | `test_pdf.py`, integration `/ask-pdf` happy path + bad uploads |
| FR-13 workspace | DB integration suite incl. path-traversal regression |
| FR-14 LangChain | `test_llm.py` (wire format, error mapping), `test_prompts.py`, `test_lc_retriever.py::test_langchain_ainvoke_matches_aretrieve` |
| FR-15 LangGraph | `test_research_graph.py` (every edge, retry bound, error states, structure) |
| NFR-04 security | `test_storage.py`, `test_auth.py`, `test_prompts.py` (delimiter injection), `test_health.py` (spoofed XFF), integration (503 without internal detail) |

## Fixtures and test data
- **Corpus:** the shipped library (`data/documents.py`, 25 abstracts) — small, versioned, reproducible.
- **arXiv:** inline Atom feeds incl. a hostile entry (`javascript:` id, injected instructions) and an entry without a title.
- **LLM:** spec-conformant chat-completion JSON built by `completion()`; scripted per host by `FakeNet`.
- **PDF:** generated in-test with PyMuPDF (`_pdf()`), so no binary fixtures.
- **DB:** `db/local_shim.sql` emulates Supabase `auth` schema; migrations applied by `scripts/migrate.py`.

## External-service mocking

| Service | Mechanism |
|---|---|
| LLM providers | `create_app(http_transport=MockTransport(...))`; the OpenAI SDK's `httpx2` calls are bridged through the app's `httpx` client, so the mock sees real request bodies (asserted in `test_llm.py`) |
| arXiv | Same transport, host `export.arxiv.org` |
| Embedding model | `EMBED_BACKEND=hash` |
| Supabase Auth | HS256 JWTs minted with a test secret |

## Regression testing
- The 25 baseline tests run unchanged (except the documented fixture correction) on every change.
- Ranking equivalence of the retriever move was checked once against the baseline commit on 48 query cases (development log, session 2) — re-run that script if ranking code changes.
- CI (`.github/workflows/ci.yml`) runs lint + the full suite on Python 3.11 and 3.13 with a pgvector service, then builds the image and runs the smoke script against the container.

## Not covered (by design or environment)
- Live LLM providers, live arXiv, real embedding model: blocked in the build environment; covered by the manual smoke procedure with real keys (TEST_REPORT §5).
- Answer *quality* (relevance, faithfulness): needs an evaluation set — Phase 2 item P2-01.
- Load/performance testing: not in Phase 1 scope.
