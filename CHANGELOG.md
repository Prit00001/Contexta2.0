# Changelog

All notable changes to Contexta 2.0. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.0.0-phase1] — 2026-10-16

Contexta 2.0 is a separate project seeded from `Prit00001/Contexta` @ `3f59589` (history preserved).

### Added
- **LangGraph research workflow** (`services/research_graph.py`): validate → retrieve → generate → validate citations → finalize, with insufficient-evidence and extractive-fallback paths, one feedback-driven regeneration (bounded by `LLM_MAX_ATTEMPTS`), an explicit error state and a per-stage trace.
- **LangChain adapters**: `ChatOpenAI` per provider over a bridge to the shared HTTP client (`core/llm.py`); `ChatPromptTemplate` with untrusted-source framing (`core/prompts.py`); `BaseRetriever` adapters for the FAISS library + arXiv and for uploaded PDFs (`core/lc_retriever.py`); source normalisation and `Document` mapping (`core/sources.py`).
- **Citation validation** (`core/citations.py`): hallucinated markers removed and reported, grouped markers normalised, claims labelled `cited` / `uncited`.
- `/ask` and `/ask-pdf` response fields (additive): `request_id`, `outcome`, `citations`, `claims`, `citation_check`, `workflow`, and per-source `marker`, `document_id`, `chunk_id`.
- `GET /ready` readiness endpoint (`ready` / `degraded` / `not_ready`).
- Structured logging (`LOG_FORMAT=json`), `X-Request-ID` propagation, generic 500 body with request ID, secret-redaction backstop.
- OpenAPI request/response examples and documented error responses.
- New settings: `REQUIRE_LLM`, `LLM_MAX_ATTEMPTS`, `TRUST_PROXY_HEADERS`, `LOG_LEVEL`, `LOG_FORMAT`.
- `scripts/smoke_test.py`, `scripts/fake_llm_server.py` (offline OpenAI-compatible stub), `Makefile`, CI workflow, `pyproject.toml` (ruff/pytest).
- Test suite grew from 25 to 181 tests: unit, mocked-network integration, real-socket E2E.
- Documentation set: requirements, architecture + diagrams, ADR-001, plans, testing, operations, Phase 2 backlog, Phase 3 research brief, completion report.

### Changed
- Configuration is a validated, frozen Pydantic model; invalid values stop startup with `ConfigError` naming the variable (previously silently replaced by defaults).
- Ranking/merging logic moved from `SearchService` into `ResearchRetriever` — behaviour verified identical to the baseline on 48 query cases.
- Dependencies: `requirements.in` → fully pinned `requirements.txt` / `requirements-dev.txt`; fastembed held on 0.7.x for a single tested version across Python 3.11–3.13.
- Dockerfile installs from the lockfile, adds `HEALTHCHECK`, JSON logs; Compose gains a one-shot `migrate` service and optional `.env`.
- A database that is unreachable at startup no longer prevents the public API from starting.

### Fixed
- **Security:** upload filenames could write outside the storage root / into another workspace's prefix (path traversal); now sanitised and storage paths confined.
- **Security:** workspace JWT verification now fails closed (503) when `SUPABASE_JWT_SECRET` is unset, independent of the PyJWT version; 401 messages no longer echo library errors.
- **Security:** rate limiter keyed on the client-controlled left-most `X-Forwarded-For` entry; now uses the proxy-appended right-most hop.
- **Privacy:** ONNX Runtime telemetry (observed connecting to `mobile.events.data.microsoft.com`) disabled by default.
- Embedding-model load failure was invisible (`/health` said `ok`); now reported by `/ready` and the startup log.
- Workspace uploads are read with a size cap instead of buffering the whole body first.
- Retrieved/uploaded text is delimited and declared untrusted in the prompt (prompt-injection hardening).
