# Contexta 2.0 — Phase 1 Completion Report

| | |
|---|---|
| Project | Contexta 2.0 (separate project seeded from `Prit00001/Contexta` @ `3f59589`, history preserved, original repo untouched) |
| Branch / commit | `phase-1` — one commit per milestone on top of the baseline (`git log --oneline main..phase-1`); not pushed (no remote configured) |
| Date | 2026-10-16 |
| Verdict | **PASS with conditions** — all Must requirements met and verified offline; live-provider, live-arXiv, real-model and Docker/CI verification are outstanding because the build environment blocked those hosts (§15) |

## 1. Executive summary
Contexta's research path now runs as an explicit LangGraph workflow (validate → retrieve → generate → validate citations → finalize) over LangChain adapters for the existing FAISS + arXiv retrieval and the existing multi-provider LLM chain. Every answer reports its `outcome`, which retrieved source each `[n]` refers to, which sentences are uncited, and which hallucinated markers were removed. Configuration is validated at startup, readiness is observable, logs are structured and privacy-preserving, and four security defects found in the audit are fixed. The test suite grew from 25 to 181 tests (unit, mocked-network integration, real-socket E2E), all passing on Python 3.11 and 3.13 from a fresh clone. Retrieval behaviour was proven unchanged against the baseline on 48 query cases.

## 2. Initial repository assessment
Baseline (audited before any change): FastAPI app with `/ask` (25-paper FAISS library + arXiv, provider chain Groq → Gemini → OpenRouter → custom with cooldowns, extractive fallback), `/ask-pdf`, and a multi-tenant workspace platform (Supabase JWT/API keys, pgvector + FTS hybrid retrieval, ingestion worker). 25/25 tests passed (incl. DB tests, run against a locally built pgvector). Embeddings via fastembed (ONNX) rather than sentence-transformers/torch. No LangChain/LangGraph.

Defects found (details: DEVELOPMENT_LOG session 1):

| ID | Defect | Status |
|---|---|---|
| D-01 | Upload path traversal (reproduced) | Fixed |
| D-02 | JWT verification relied on PyJWT version when secret unset | Fixed |
| D-03 | Silent embedding fallback; no readiness | Fixed (`/ready`, startup log) |
| D-04 | Invalid env values silently defaulted | Fixed (`ConfigError`) |
| D-05 | Weak citation handling; no document/chunk IDs | Fixed (marker-level; entailment → Phase 2) |
| D-06 | Rate limit bypass via spoofed `X-Forwarded-For` | Fixed |
| D-07 | No structured logs / request IDs / safe 500 | Fixed |
| D-08 | Sources not framed as untrusted in prompt | Fixed |
| D-09 | Stale README | Fixed |
| D-10 | No lockfile | Fixed |
| D-11 | ONNX Runtime telemetry to `mobile.events.data.microsoft.com` (found during smoke runs) | Fixed (opt-out by default) |

## 3. Requirements completed
All 15 functional and 8 non-functional requirements in the PRD are implemented; see the traceability matrix (§12). FR-01 … FR-15 met; NFR-06 (Docker part) and the live-provider aspect of FR-05/FR-07 are verified only offline.

## 4. Architecture implemented
Single FastAPI service, `app → services → core` layering preserved ([SYSTEM_DESIGN](architecture/SYSTEM_DESIGN.md), [ADR-001](decisions/ADR-001-project-architecture.md)). LangChain is confined to `core/llm.py`, `core/prompts.py`, `core/lc_retriever.py`, `core/sources.py`; LangGraph to `services/research_graph.py`. The graph structure is asserted by a test and its code-generated Mermaid diagram is in [langgraph-workflow.md](architecture/diagrams/langgraph-workflow.md).

## 5. Components and APIs delivered

| Component | File | Notes |
|---|---|---|
| Research workflow | `services/research_graph.py` | 7 nodes, 4 conditional routers, bounded retry with feedback, error state, trace |
| LLM adapter | `core/llm.py` | `ChatOpenAI` per provider, `httpx2`→`httpx` bridge, SDK error → cooldown mapping |
| Prompt | `core/prompts.py` | `ChatPromptTemplate`, `<source>` fencing, delimiter neutralisation |
| Retrievers | `core/lc_retriever.py` | `ResearchRetriever`, `PdfChunkRetriever` (`BaseRetriever`) |
| Source metadata | `core/sources.py` | IDs, URL validation, `Document` mapping |
| Citations | `core/citations.py` | marker validation, claims, report |
| Config | `app/config.py` | validated Pydantic settings |
| Observability | `app/observability.py` | logging, request IDs, 500 handling, redaction |
| Smoke tooling | `scripts/smoke_test.py`, `scripts/fake_llm_server.py` | |

| Endpoint | Change |
|---|---|
| `POST /ask` | Runs the graph; additive fields `request_id, outcome, citations, claims, citation_check, workflow`, per-source `marker, document_id, chunk_id`; OpenAPI examples |
| `POST /ask-pdf` | Same, plus filename sanitisation |
| `GET /health` | Unchanged contract |
| `GET /ready` | **New** |
| `/workspaces/...` | Contract unchanged; security fixes |

## 6. Dependencies added or changed

| Dependency | Version (lock) | Why | Maintenance cost |
|---|---|---|---|
| `langgraph` | 1.2.15 | Explicit, testable workflow (FR-15) | Moderate; pulls `langgraph-checkpoint`, `langgraph-sdk`, `langgraph-prebuilt` (unused) |
| `langchain-core` | 1.6.11 | Prompt templates, `BaseRetriever`, `Document`, messages (FR-14) | Moderate; pulls `langsmith` (tracing off unless env-enabled), `pyyaml` |
| `langchain-openai` | 1.7.2 | `ChatOpenAI` for all OpenAI-compatible providers | Pulls `openai` and `tiktoken` |
| `openai` | 3.31.0 | Imported directly for exception classes | Major-version churn risk |
| `httpx2` | 2.13.1 | Imported directly: transport type the OpenAI SDK v3 requires | New package; watch for API changes |
| `pydantic` | 2.14.1 | Now an explicit direct dependency (config) | Already present via FastAPI |
| `ruff` (dev) | 0.17.1 | Lint + format gate | Low |
| `fastembed` | held at 0.7.3 | 0.9 resolves only for Python < 3.13; one tested version everywhere | Revisit when 0.9+ supports 3.13 |

All versions pinned in `requirements.txt` (81 packages) and `requirements-dev.txt` (86), compiled with `uv pip compile --universal --python-version 3.11`.

## 7. Test commands and actual results
From [TEST_REPORT](testing/TEST_REPORT.md) (fresh clone of `af4acb0`, fresh venv from the lock):

| Command | Result |
|---|---|
| `pytest -q` (3.13) | 178 passed, 3 skipped (DB) |
| `DATABASE_URL=… pytest -q` (3.13) | **181 passed** |
| `DATABASE_URL=… pytest -q` (3.11) | **181 passed** |
| `ruff check .` / `ruff format --check .` | clean / 59 formatted (26 legacy excluded) |

## 8. Manual smoke-test evidence
- Default config, clean setup per README: startup 43 s (offline model-download retries), smoke **12/12**, `/ready` `degraded` with the right reasons.
- Stub LLM provider: smoke **13/13**, `/ready` `ready`; LoRA question answered from `doc_009#0` with a validated citation; full per-stage JSON log trail under `X-Request-ID: demo-001`.
- Ranking equivalence vs baseline: 48/48 identical.

## 9. Deployment and startup instructions
Local: `pip install -r requirements-dev.txt && uvicorn app.main:app`. Docker: `docker build -t contexta . && docker run -p 8000:8000 --env-file .env contexta`. Compose: `docker compose up --build`. Health: `/health` (liveness), `/ready` (readiness). Full procedure, rollback and limits: [DEPLOYMENT](operations/DEPLOYMENT.md); configuration: [CONFIGURATION](operations/CONFIGURATION.md).

## 10. Known defects and limitations
1. Citation validation is marker-level only (`verification: marker_only`) — a cited claim may still be unsupported by its source.
2. Live LLM providers, live arXiv and the real embedding model were not exercised (blocked hosts); `max_completion_tokens` acceptance by Gemini's OpenAI-compatible endpoint is unverified.
3. Docker image not built; CI not run.
4. Offline startup waits ~45 s for fastembed's download retries before the hash fallback.
5. Caches and the `/ask` limiter are per process.
6. Workspace Q&A does not yet use the graph (no claims/citation report there).
7. The UI ignores the new citation fields (works, but doesn't show claim status).
8. 26 legacy files are excluded from the format check; import-sorting lint not enabled.

## 11. Security considerations
Fixed: path traversal (D-01), JWT fail-open risk (D-02), rate-limit spoofing (D-06), prompt-injection framing (D-08), telemetry (D-11). In place: env-only secrets, `repr`-excluded secret fields, no secrets in `/health`/`/ready`/logs (tested), redaction backstop, generic 500s with request IDs, request/upload size limits, http(s)-only source URLs, defused XML, no tools/code execution for the LLM, HTML-escaping UI. By design: `/ask` unauthenticated (public demo, rate-limited) — documented decision A-01. Residual: sophisticated prompt injection can still influence answer *wording* (not actions); the model has no capabilities beyond producing text.

## 12. Requirements traceability matrix

| Req | Implementation | Verification | Status |
|---|---|---|---|
| FR-01 Startup | `app/main.py` lifespan; README | Fresh-clone startup (TR §3.1); E2E uvicorn | ✅ |
| FR-02 Health/readiness | `routes.health`, `routes.ready` | `test_health.py` (7), integration cooldown | ✅ |
| FR-03 Query API | `AskRequest`/`AskResponse`, `routes.ask` | `test_api.py`, integration happy path + validation | ✅ |
| FR-04 Retrieval + IDs | `core/lc_retriever.py`, `core/sources.py` | `test_lc_retriever.py` (12), `test_sources.py` (17), equivalence 48/48 | ✅ |
| FR-05 Grounded generation | prompt rules, graph insufficient-evidence path, marker stripping | `test_research_graph.py`, integration (empty retrieval, hallucinated markers) | ✅ offline · live ⏳ B-3..B-5 |
| FR-06 Attribution | `core/citations.py`, `safe_url` | `test_citations.py`, integration (malformed metadata) | ✅ (marker-level) |
| FR-07 arXiv | `ResearchRetriever` + existing client | 5 failure modes + empty + disabled (unit + integration) | ✅ offline · live ⏳ B-2 |
| FR-08 Provider config | `app/config.py` | `test_config.py` (15), key-never-logged test, REQUIRE_LLM startup | ✅ |
| FR-09 OpenAPI | schemas examples, `responses=` | `test_openapi_has_examples_and_error_responses` | ✅ |
| FR-10 Observability | `app/observability.py`, stage events | `test_observability.py` (12), TR §3.2 log trail | ✅ |
| FR-11 UI | `app/static/index.html` unchanged; compatible response | baseline `test_api.py`; field-compat review | ✅ |
| FR-12 PDF Q&A | `PdfChunkRetriever`, `/ask-pdf` | `test_pdf.py`, integration PDF tests | ✅ |
| FR-13 Workspace platform | preserved + security fixes | 3 DB integration tests incl. traversal | ✅ |
| FR-14 LangChain | 4 adapter modules | `test_llm.py` (20), `test_prompts.py`, `ainvoke` parity test | ✅ |
| FR-15 LangGraph | `services/research_graph.py` | `test_research_graph.py` (18) | ✅ |
| NFR-01 Never-down | extractive fallback | graph + integration fallback tests | ✅ |
| NFR-02 Latency bounds | `ARXIV_BUDGET_S`, `LLM_TIMEOUT`, `max_retries=0` | budget-exceeded test, SDK-retries-disabled test | ✅ |
| NFR-03 Memory | no torch added | dependency review | ✅ (not measured) |
| NFR-04 Security | §11 | `test_storage.py` (21), `test_auth.py`, injection & XFF tests | ✅ |
| NFR-05 No-network tests | `MockTransport`, hash embeddings | suite run with external hosts blocked | ✅ |
| NFR-06 Reproducibility | lockfiles, Dockerfile, Compose | fresh installs 3.11 + 3.13 | ✅ · Docker ⏳ B-6 |
| NFR-07 Maintainability | ruff, adapters | `ruff check`/`format --check` | ✅ |
| NFR-08 Privacy | question fingerprinting, telemetry off | log tests, telemetry subprocess test, proxy log | ✅ |

## 13. Decisions and trade-offs
Kept the custom provider chain (cooldowns) instead of `with_fallbacks()`; bridged `httpx2` to keep one client and mockability; linear graph with four conditional routers rather than an agent; FAISS kept (25 docs); additive API changes only; no mass reformat; fastembed held at 0.7.x; marker-level citation check stated explicitly in every response rather than over-claiming verification. Rationale: ADR-001 and DEVELOPMENT_LOG session 2.

## 14. Deferred work
- **Phase 2:** [backlog](roadmap/PHASE_2_BACKLOG.md) — evaluation set first (P2-01), threshold calibration, live-provider verification, claim-support checking, evidence UX, hybrid/BM25, reranking, workspace path on the graph, shared caches, metrics, fast-fail model loading.
- **Phase 3:** [research brief](research/PHASE_3_QUERY_REWRITING_RESEARCH.md) — four papers verified and summarised; staged experiment (6 conditions) and Ollama evaluation protocol.

## 15. Pass/fail assessment against the Definition of Done

| Definition of Done item | Assessment | Evidence |
|---|---|---|
| Runs from a clean, documented setup | **PASS** | Fresh clone + README steps, TR §3.1 |
| Complete research-query workflow executes | **PASS** | E2E over real sockets; stub-LLM smoke 13/13 |
| LangChain and LangGraph meaningfully integrated | **PASS** | All LLM calls, prompts, retrieval interfaces and the request workflow use them |
| Retrieval and generation work with configured providers | **PASS (offline) / PENDING (live)** | Real OpenAI-compatible HTTP to the stub; provider wire format and errors tested via mocks; live keys not usable here (B-1…B-5) |
| Sources returned with validated metadata | **PASS** | IDs + URL validation tests; integration hostile-metadata test |
| Failure cases handled | **PASS** | 18 integration scenarios incl. every brief-listed failure |
| Automated tests and smoke tests executed | **PASS** | 181/181 on 3.11 and 3.13; smoke 12/12 and 13/13 |
| Documentation complete and consistent | **PASS** | Full set present; checked against code (endpoints, env vars, counts) |
| Outstanding defects and limitations disclosed | **PASS** | §10, TEST_REPORT §4–5 |
| Completion report with RTM and evidence | **PASS** | This document |

**Overall: Phase 1 PASS, conditional on B-1…B-7** (TEST_REPORT §5): run the smoke script once with real keys and network, build the Docker image, and run CI after pushing to GitHub. These take minutes on a normal machine and require no code changes unless a provider rejects a request.
