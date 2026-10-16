# Test Report — Phase 1

| | |
|---|---|
| Commit under test | `af4acb0` (branch `phase-1`), fresh `git clone`, fresh virtualenv from `requirements-dev.txt` |
| Date | 2026-10-16 |
| Environment | Linux sandbox; Python 3.13.16 and 3.11.17; Postgres 16.15 + pgvector 0.8.0 (local cluster); outbound network limited to package registries and GitHub |

Every result below was produced by an executed command. Nothing is reported as passing that was not run.

## 1. Automated tests

| Command | Python | Result |
|---|---|---|
| `pytest -q` | 3.13.16 | **178 passed, 3 skipped** (DB tests skip without `DATABASE_URL`) |
| `DATABASE_URL=postgresql://postgres@localhost:55432/contexta pytest -q` | 3.13.16 | **181 passed** |
| same | 3.11.17 (Docker's interpreter) | **181 passed** |
| `ruff check .` | 3.13 | All checks passed |
| `ruff format --check .` | 3.13 | 59 files already formatted (26 legacy files excluded — see `pyproject.toml`) |

Baseline before Phase 1 (`main` @ `3f59589`): 25 passed (22 + 3 DB).

### Per file (181 with DB)

| File | Tests | Level |
|---|---|---|
| tests/test_storage.py | 21 | unit (security) |
| tests/test_llm.py | 20 | unit (LangChain adapter) |
| tests/test_research_graph.py | 18 | unit (LangGraph routes) |
| tests/integration/test_research_flow.py | 18 | integration (mocked network) |
| tests/test_sources.py | 17 | unit |
| tests/test_config.py | 15 | unit |
| tests/test_lc_retriever.py | 12 | unit (retriever + arXiv failures) |
| tests/test_observability.py | 12 | unit/API |
| tests/test_citations.py | 7 | unit |
| tests/test_health.py | 7 | API |
| tests/test_auth.py | 5 | unit/API (3 baseline + 2 new) |
| tests/test_prompts.py | 5 | unit |
| tests/test_generator.py | 4 | unit (baseline) |
| tests/test_parsers.py | 4 | unit (baseline) |
| tests/test_pdf.py | 4 | unit (baseline) |
| tests/test_api.py | 3 | API (baseline) |
| tests/test_chunking.py | 3 | unit (baseline) |
| tests/e2e/test_smoke_e2e.py | 2 | E2E (real processes) |
| tests/test_arxiv.py | 1 | unit (baseline) |
| tests/integration/test_ingestion_pipeline.py | 1 | DB integration (baseline) |
| tests/integration/test_tenant_isolation.py | 1 | DB integration (baseline) |
| tests/integration/test_workspace_api.py | 1 | DB integration (baseline, extended with path-traversal checks) |

## 2. Known failures
None. No test is skipped except the 3 DB tests when `DATABASE_URL` is unset; no test was disabled or weakened. One baseline fixture was corrected (mock completion gained `"role": "assistant"`), see DEVELOPMENT_LOG session 2.

## 3. Manual verification

### 3.1 Clean setup following the README (default config, no keys)
`uvicorn app.main:app --port 8201` from the fresh clone.
- Startup to first `/health` response: **43 s** — fastembed retried the blocked model download, then fell back to hash embeddings. Startup log: `Ready: 25 docs, embeddings=hash (FALLBACK), LLM providers=none (extractive mode), db=not configured`.
- `python scripts/smoke_test.py --base-url http://127.0.0.1:8201` → **12/12 passed**; `/ready` = `degraded` with reasons `embeddings … hash fallback` and `llm=no provider configured`.
- README `curl` example → 200, `outcome: extractive_fallback`, warning "Live arXiv search was unavailable…" (arXiv blocked), all documented response keys present.

### 3.2 Full LLM path with the stub provider
`scripts/fake_llm_server.py` + `LLM_PROVIDERS=custom LLM_BASE_URL=http://127.0.0.1:9009/v1 LLM_MODEL=fake-llm REQUIRE_LLM=true LOG_FORMAT=json`:
- Smoke script with `--expect-llm` → **13/13 passed**; `/ready` = `ready` (all checks ok); repeat question served from cache.
- `POST /ask {"question":"What does LoRA freeze during fine-tuning?","top_k":2,"use_arxiv":false}` with `X-Request-ID: demo-001` → header echoed; `outcome: answered`, provider `custom`, source `[1] LoRA: Low-Rank Adaptation of Large Language Models` (`doc_009#0`), claim `cited`, `citation_check.valid_markers = 1`.
- JSON log lines for that request: `workflow.stage` × 5 (validate_request, retrieve, generate, validate_answer, finalize), `llm.call` (ok), `ask.result` (with `question_len`/`question_sha8`, no question text), `http.request` (200, `duration_ms`).

### 3.3 Other checks
| Check | Result |
|---|---|
| Dockerfile `HEALTHCHECK` command against a running server | exit 0 |
| Dockerfile `CMD` (`sh -c "uvicorn … --port ${PORT:-8000}"`) with `PORT=8202` | server up, `/health` 200 |
| `LLM_TIMEOUT=abc` | startup fails: `ConfigError: Invalid configuration - LLM_TIMEOUT: Input should be a valid number…` |
| `REQUIRE_LLM=true`, no keys | startup fails: `… REQUIRE_LLM=true but no LLM provider is configured …` |
| Secret scan of tracked files (key-shaped regexes) | only the fake test value `gsk_supersecretvalue123456` in `tests/test_observability.py` |
| `.env` ignored / tracked | `git check-ignore .env` → `.env`; not tracked |
| `docker compose config -q` | valid |
| Ranking equivalence vs baseline (48 cases) | identical (session 2 log) |
| ONNX telemetry with `ORT_DISABLE_TELEMETRY=1` | 0 connections to `mobile.events.data.microsoft.com` (3 without) |

## 4. Environment limitations
The sandbox's egress allowlist blocks: `export.arxiv.org`, all LLM provider APIs, `huggingface.co` / `storage.googleapis.com` (embedding model), and Docker Hub. Consequences:
- Retrieval quality in manual runs is that of **hash embeddings**, not MiniLM — e.g. only 1 source above `MIN_SCORE` for the RAG question. This is not representative of production retrieval.
- The Docker image was **not built**; even `docker build --check` fails to resolve the base image.
- CI was **not executed** (no GitHub remote configured).

## 5. Blocked tests — to run with network access and real keys

| # | Test | Command | Expected |
|---|---|---|---|
| B-1 | Real embedding model | `uvicorn app.main:app` then `GET /ready` | `embeddings.ok = true`, backend `fastembed` |
| B-2 | Live arXiv | `scripts/smoke_test.py` + `curl /ask` with `"use_arxiv": true` | arXiv sources present, no arXiv warning |
| B-3 | Groq | `GROQ_API_KEY=… LLM_PROVIDERS=groq` + `smoke_test.py --expect-llm` | 13/13, provider `groq` |
| B-4 | Gemini (incl. `max_completion_tokens` acceptance) | `GEMINI_API_KEY=… LLM_PROVIDERS=gemini` + smoke | 13/13; if 400, set a different model or report |
| B-5 | OpenRouter | `OPENROUTER_API_KEY=… LLM_PROVIDERS=openrouter` + smoke | 13/13 |
| B-6 | Docker | `docker build -t contexta . && docker run -p 8000:8000 contexta` + smoke | 12/12 (13 with a key); `/ready` embeddings ok |
| B-7 | CI | push to GitHub | lint, test (3.11, 3.13), docker jobs green |
