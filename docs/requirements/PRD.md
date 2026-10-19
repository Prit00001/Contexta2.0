# Contexta 2.0 — Product Requirements Document (Phase 1)

| | |
|---|---|
| Status | Approved for Phase 1 implementation |
| Project | Contexta 2.0 — a separate project seeded from `Prit00001/Contexta` (history preserved; the original repo is not modified) |
| Baseline | `main` @ `3f59589` (audited 2026-10-16) |
| Owner | Prit00001 |

## 1. Problem statement

Researchers and students who want a quick, trustworthy answer to an ML/NLP question face two bad options: a general chatbot that answers fluently but invents papers and citations, or a manual literature search that is slow. Contexta answers research questions **only from retrieved evidence** (a curated paper library, live arXiv results, or a document the user supplies) and shows which source backs each statement, so the user can check the answer instead of trusting it.

## 2. Target users and use cases

| User | Need | Primary use case |
|---|---|---|
| ML/NLP student or engineer | Understand a concept quickly, with papers to read next | UC-01 Ask a research question |
| Researcher with a specific paper | Ask questions about one PDF | UC-02 Ask about an uploaded PDF |
| Team (existing workspace platform) | Grounded Q&A over the team's own documents | UC-04 Workspace Q&A |
| Operator / developer | Run, monitor and configure the service | UC-03 Check health/readiness, UC-05 Configure providers |
| Recruiter / interviewer | See a working, explainable AI system | UC-01 via UI or API docs |

Full scenarios: [use-cases.md](use-cases.md).

## 3. Functional requirements

Priority: **M** = Must (Phase 1 cannot pass without it), **S** = Should, **C** = Could.

| ID | Requirement | Priority | Acceptance criterion |
|---|---|---|---|
| FR-01 | Application starts with documented commands and configuration | M | `uvicorn app.main:app` starts from a clean venv following the README; startup verified and recorded in TEST_REPORT |
| FR-02 | Health (liveness) and readiness endpoints; no secrets in responses | M | `GET /health` → 200 while the process runs; `GET /ready` → 200 `ready`/`degraded` or 503 `not_ready` with per-check detail; tests assert no key material in either body |
| FR-03 | Research query API with validated request and structured response | M | `POST /ask` rejects invalid input with 422; success returns `answer`, `sources`, `citations`, `request_id`, timings, provider info |
| FR-04 | Retrieval via existing FAISS library + arXiv; preserve metadata; distinguish documents from chunks | M | Every source carries `document_id` and `chunk_id`; retrieval adapter tests pass |
| FR-05 | Grounded generation; no fabricated metadata/URLs/citations; communicate insufficient evidence | M | Source metadata comes only from retrieval; out-of-range citations removed; empty retrieval returns an explicit insufficient-evidence answer without calling the LLM |
| FR-06 | Source attribution with validated metadata; distinguish validated citations from unverified claims | M | Response contains `citations[]` (marker → source) and `claims[]` with `status` `cited`/`uncited`; URLs validated as http(s) |
| FR-07 | Live arXiv search preserved; failures, timeouts, empty and malformed responses handled | M | Mocked tests for HTTP 5xx, timeout, empty feed, malformed XML each return a 200 answer from the library with a warning |
| FR-08 | Provider credentials/model config from environment; fail clearly on missing/invalid config; never log keys | M | Invalid numeric/boolean env → startup error naming the variable; `REQUIRE_LLM=true` with no provider → startup error; log-capture test shows no key |
| FR-09 | OpenAPI docs with request/response examples incl. failures | S | `/openapi.json` contains examples for `/ask` 200, 422, 429 |
| FR-10 | Observability: structured events, durations, failure categories, workflow stages; no secrets; minimal user content | S | JSON log lines per request and per workflow stage with `request_id`, `duration_ms`, `category`; question logged only as length + hash |
| FR-11 | Basic UI preserved and working | S | Existing `app/static/index.html` still renders `/ask` and `/ask-pdf` responses (response shape backward compatible) |
| FR-12 | Uploaded-PDF Q&A (`/ask-pdf`) preserved | S | Existing PDF tests pass; responses carry FR-04/FR-06 metadata |
| FR-13 | Workspace platform (auth, upload, ingestion, workspace Q&A) preserved | S | Existing DB integration tests pass against Postgres+pgvector |
| FR-14 | LangChain integration through clear interfaces (LLM, prompt, documents, retriever) | M | LangChain types confined to `core/llm.py`, `core/prompts.py`, `core/lc_retriever.py`; rest of code uses project types |
| FR-15 | LangGraph workflow: validate → retrieve → generate → validate citations → respond, with bounded retry and explicit error states | M | Graph routing unit tests for each edge; retry bound asserted |

## 4. Non-functional requirements

| ID | Requirement | Priority | Measure |
|---|---|---|---|
| NFR-01 | Reliability: the app answers even when every LLM provider fails | M | Extractive fallback test passes |
| NFR-02 | Latency: arXiv time budget ≤ 9 s; LLM timeout configurable (default 25 s) | M | Config defaults + timeout tests |
| NFR-03 | Memory: fits a 512 MB instance with fastembed (no torch) | S | No torch dependency added |
| NFR-04 | Security: request size limits, safe error messages, untrusted-document framing, filename sanitisation | M | Tests for path traversal, oversize input, prompt framing |
| NFR-05 | Testability: default test suite needs no network and no paid API | M | Suite passes with network blocked |
| NFR-06 | Reproducibility: pinned dependency lockfile; documented setup; Docker | M | `requirements.txt` fully pinned; `requirements.in` lists direct deps |
| NFR-07 | Maintainability: lint passes; framework code isolated | S | `ruff check` clean |
| NFR-08 | Privacy: question text not stored or logged verbatim by default | S | Logging test |

## 5. Scope

**In scope (Phase 1):** everything in §3–§4, documentation set, CI workflow definition.

**Out of scope (deferred):** hybrid sparse retrieval for `/ask`, Qdrant, reranking models, evaluation datasets (Phase 2); query rewriting and local Ollama inference (Phase 3); streaming; conversation history; new frontend framework; authentication for the public `/ask` endpoint (see SRS §6).

## 6. Assumptions

- A-01: The public `/ask` endpoint remains unauthenticated, protected by per-IP rate limiting, as in the existing product. Workspace endpoints keep their existing auth.
- A-02: "OpenRouter as initial provider" is satisfied by the existing multi-provider chain (OpenRouter is one link); the default order stays `groq,gemini,openrouter,custom` so existing deployments are not broken. OpenRouter-only is one env var away (`LLM_PROVIDERS=openrouter`).
- A-03: The existing embedding stack (fastembed ONNX running `sentence-transformers/all-MiniLM-L6-v2`) is the "existing Sentence Transformers embeddings"; we do not reintroduce torch.
- A-04: Live arXiv/LLM/model-download behaviour cannot be verified in the build sandbox (network allowlist); it is verified with mocked HTTP and must be smoke-tested by the owner with real keys.
- A-05: Citation "validation" in Phase 1 means a citation marker refers to a source that was actually retrieved and shown. Whether the cited text *entails* the claim is not verified in Phase 1 and is labelled as such.

## 7. Phase 1 acceptance criteria (summary)

Phase 1 passes when all **Must** requirements meet their acceptance criteria above, the full test suite passes, the server starts and answers a research query end-to-end in a reproducible smoke test, and the documentation set matches the code. Evidence is recorded in `docs/testing/TEST_REPORT.md` and `docs/PHASE_1_COMPLETION_REPORT.md`.
