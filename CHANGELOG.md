# Changelog

All notable changes to Contexta 2.0. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — Phase 3 (query rewriting)

### Added
- `QUERY_REWRITE = off | single | multi | decompose | selective` (default `off`): the research brief's rewriting conditions as a `rewrite` node in the LangGraph workflow; at most one LLM call per request; original query kept and RRF-fused except in `single`; rewrites never query arXiv; failed rewrites fall back to the original question ([ADR-004](docs/decisions/ADR-004-query-rewriting.md)).
- `REWRITE_PROVIDERS`: a separate provider chain for rewriting (local Ollama vs hosted, condition 6). `REWRITE_GAP_SCORE` for `selective` (uncalibrated).
- `query_rewrites` in `/ask`, `/ask-pdf` and workspace responses; "Also searched for" line in the UI.
- `eval/run_eval.py --rewrite all|<modes>` (`eval/rewrite_eval.py`): per-condition retrieval metrics, LLM calls, rewrite latency, abstention, similarity-to-gold delta.
- `scripts/fake_llm_server.py` answers rewrite prompts deterministically, for offline demos.

- Phase 3 harness: `--retrievers hybrid,hybrid_bm25,dense,keyword` and `--decoys` (keyword-stuffed decoy abstracts, brief §3.4); offline hash-embedding baseline in EVALUATION_REPORT §5c.
- **`POST /ask/stream`** (P2-13): server-sent events (`stage`, `sources`, `token`, `reset`, `done`, `error`) over the same workflow, cache and rate limit as `/ask`; `done` is the `/ask` response. The UI streams library answers. The LLM HTTP bridge passes provider SSE through chunk by chunk; the fake LLM server streams with `--delay`.

- **Reranking** (P2-07), `RERANK_MODEL` (off by default): fastembed ONNX cross-encoder reorders the top `RERANK_CANDIDATES` sources that passed `MIN_SCORE`; `/ready` reports load failures; `eval/run_eval.py --rerank`, and retrieval p95 latency in the eval summary.
- **Claim-checker measurement tooling**: `--with-llm` + `CLAIM_CHECK=llm` writes `eval/claims_to_label.csv`; `--score-claims` scores the checker against human labels.

- **Follow-up questions without server-side history** (P2-14, stateless part): optional `history` (≤3 turns) on `/ask` and `/ask/stream`; `contextualize` graph node (one LLM call, merge fallback); `standalone_question` in responses; history fenced as untrusted, included in the cache key, never stored or logged. UI: in-memory turns, "New topic", "Read as" line.

### Changed
- `Generator.judge` renamed `Generator.complete` (shared by claim checking and rewriting).

## [Unreleased] — Phase 2

### Added
- **Evaluation harness** (`eval/run_eval.py`, `make eval`): 143-question set (LLM-drafted, `reviewed: false`), retrieval metrics, opt-in `--with-llm` answer metrics; real-embedding baseline in `docs/evaluation/EVALUATION_REPORT.md` (ADR-002).
- **`MIN_SCORE` calibration** (P2-FR-01): `--sweep-min-score` / `make eval-sweep` reports Recall@5, Precision@5, false abstention and retrieval-level abstention per threshold.
- **Claim-support checking** (P2-FR-04), `CLAIM_CHECK=llm`, off by default: `check_claims` graph node; per-claim `support` / `support_method`; `citation_check.verification = "claim_support"` with claim counts.
- **Hybrid BM25 retrieval** (P2-FR-03), `HYBRID_BM25_ENABLED`, off by default (ADR-003).
- **Shared answer cache and `/ask` rate limit in Redis** (P2-FR-07) when `REDIS_URL` is set, with in-memory fallback.
- **`GET /metrics`** (Prometheus; `METRICS_ENABLED`): answers by endpoint/outcome/cached, answer latency, per-stage durations, LLM calls by provider/status, 429s.
- **Evidence view in the web UI** (P2-05): uncited and unsupported sentences marked, removed markers reported, `[n]` opens its excerpt.

### Changed
- `MIN_SCORE` unset → per effective embedding backend: `0.2` (fastembed), `0.075` (hash, incl. the fallback).
- `/workspaces/{id}/ask` runs on the shared LangGraph workflow (P2-FR-06) and returns `outcome` / `citations` / `claims` / `citation_check` / `workflow`.
- Response schema (additive): `claims[].support`, `claims[].support_method`, `citation_check.{supported,not_supported,unknown}_claims` — `null` unless `CLAIM_CHECK=llm`.
- ruff: import sorting (`I`) and bugbear (`B`) enabled; `zip(strict=True)` where lengths must match.
- New dependency: `prometheus-client`.

### Fixed
- On hash embeddings the old `MIN_SCORE=0.2` left 26% of answerable eval questions with no sources (now 0%, measured).
- Offline start with no cached model took 46 s: the earlier "fast-fail" fix passed `retries=1` to fastembed, which 0.7 silently ignores. The downloader is now called directly with one attempt (~5 s, measured).
- `OPENROUTER_MODEL` default was a model OpenRouter's catalog no longer serves (`HTTP 404`, confirmed live); replaced with a currently-available free model.
- Test suite leaked real provider API keys from a local `.env` into test runs (`load_dotenv()` refilled env vars that `tests/conftest.py` had popped); tests are now isolated regardless of what `.env` contains.

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
