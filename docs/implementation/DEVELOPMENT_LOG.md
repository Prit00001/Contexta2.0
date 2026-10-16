# Development Log

## Session 1 — 2026-10-16 (baseline audit)

**Environment:** Linux sandbox, Python 3.13.16, Postgres 16.15 + pgvector 0.8.0 built from source (local cluster on port 55432), Redis and Tesseract present. Network allowlist permits PyPI/GitHub only: **arXiv, LLM providers, Hugging Face and Docker Hub are unreachable.**

**Project setup:** Contexta 2.0 created as a separate project (`git clone` of `Prit00001/Contexta`, history kept, `origin` removed). `main` = baseline `3f59589`; work on branch `phase-1`. The original repository was left unmodified.

**Baseline tests executed**

| Command | Result |
|---|---|
| `pytest -q` (no `DATABASE_URL`) | 22 passed, 3 skipped |
| `DATABASE_URL=postgresql://postgres@localhost:55432/contexta pytest -q` (after `scripts/migrate.py`) | **25 passed** |
| `ruff check --select E4,E7,E9,F .` | clean |
| `ruff format --check --line-length 120 .` | 34 files would be reformatted (not applied — see decisions) |

**Manual verification:** `uvicorn app.main:app` started; fastembed model download retried ~45 s then fell back to hash embeddings; `/health` → 200 reporting `embedding_backend: "hash"` but `status: "ok"`; `/docs` → 200; `/ask` "How does LoRA reduce trainable parameters?" → 0 sources (hash embeddings + `MIN_SCORE=0.2`), arXiv warning.

**Defects found**

| ID | Defect | Severity |
|---|---|---|
| D-01 | Upload filename used unsanitised in storage path; reproduced write outside `STORAGE_DIR` with `../../x.md` | High (security) |
| D-02 | Unset `SUPABASE_JWT_SECRET` relies on PyJWT ≥ 2.10 rejecting empty HMAC keys; error message leaks the config state | Medium (security) |
| D-03 | Embedding load failure silently degrades to hash vectors; `/health` still `ok`; no readiness endpoint | Medium |
| D-04 | Invalid numeric env values silently replaced by defaults | Medium |
| D-05 | Citation handling only strips out-of-range markers; no claim-level status; no document vs chunk IDs | Medium |
| D-06 | Rate limiter keys on the left-most `X-Forwarded-For` entry, which the client controls | Medium |
| D-07 | No structured logging, request IDs or generic 500 handler | Low |
| D-08 | Retrieved/uploaded text not framed as untrusted in the prompt | Medium (prompt injection) |
| D-09 | README stale: model defaults, missing workspace platform, DB, worker | Low |
| D-10 | Dependencies are ranges; no lockfile; Docker image Python 3.11 vs untested | Low |

**Decisions**
- Preserve the workspace platform; only security fixes there in Phase 1.
- Keep `/ask` response backward compatible (UI depends on `answer`, `sources[].{title,url,snippet,source,authors,published,page}`, `warnings`, `diagnostics`, `timings_ms`, `cached`, `llm_used`, `model_used`).
- Do not mass-reformat legacy files; ruff format applies to new modules.

**Outstanding:** everything in the implementation plan.

## Session 2 — 2026-10-16 (Phase 1 implementation)

Branch `phase-1`, one commit per milestone (see `git log main..phase-1`). Nothing pushed.

| Milestone | Changes | Tests executed → result |
|---|---|---|
| M0 docs | PRD, SRS, use cases, system design, diagrams, ADR-001, plan | — |
| M1 security | `safe_filename`, storage confinement, capped upload read, JWT fail-closed | `test_storage.py`, `test_auth.py`, DB API test with `../` filename → 48 passed |
| M2 config/readiness | Pydantic `Settings`, `ConfigError`, `/ready`, right-most XFF hop | `test_config.py`, `test_health.py` → 69 passed |
| M4/M6 LangChain + citations | `core/llm.py`, `core/prompts.py`, `core/citations.py`; generator on `ChatOpenAI` | 4 original generator tests unchanged → pass; `test_llm.py`, `test_prompts.py`, `test_citations.py` → 101 passed |
| M5/M7 retrieval + graph | `core/sources.py`, `core/lc_retriever.py`, `services/research_graph.py`; `/ask` + `/ask-pdf` on the graph | baseline API tests unchanged → pass; new retriever/graph/source tests → 148 passed |
| M8 observability | logging, request IDs, 500 handling, OpenAPI examples | `test_observability.py` → 160 passed |
| M9 integration/E2E | transport injection, 18 integration scenarios, stub LLM, smoke script, E2E | → 181 passed |
| M10 tooling | lockfiles, ruff config, Docker/Compose, CI, Makefile | fresh installs on 3.13 and 3.11 → all pass |
| M11 docs | README, ops, testing, backlog, research brief, completion report | fresh-clone verification (TEST_REPORT) |

**Decisions**
- Keep the provider chain in `core/generator.py`; LangChain `with_fallbacks()` has no cross-request cooldown (ADR-001).
- `HttpxBridgeTransport`: the OpenAI SDK v3 under `langchain-openai` uses `httpx2`; bridging keeps one client and makes the SDK mockable with `httpx.MockTransport`.
- Corrected one baseline test fixture: mock completion now includes `"role": "assistant"` (spec-conformant; LangChain requires it).
- Retry sends explicit feedback rather than an identical prompt.
- fastembed pinned to 0.7.x so Python 3.11 (Docker) and 3.13 run the same tested version (the universal lock otherwise chose 0.9.0 for < 3.13).
- Legacy files excluded from `ruff format` instead of mass-reformatting (26 files listed in `pyproject.toml`).

**Verification beyond unit tests**
- *Ranking equivalence:* baseline commit (`git worktree` of `main`) vs `phase-1`, hash embeddings, 12 questions × `top_k` {3, 5} × `MIN_SCORE` {0.0, 0.2}: sources, scores and extractive answers identical in 48/48 cases.
- *Manual smoke runs:* default config (no keys, model download blocked → `degraded`) 12/12; stub-LLM config 13/13 with `ready` on all checks; JSON logs show every workflow stage under the client-supplied `X-Request-ID`.
- *Telemetry finding:* proxy logs showed ONNX Runtime connecting to `mobile.events.data.microsoft.com` during model loading; `onnxruntime.disable_telemetry_events()` did **not** stop it, `ORT_DISABLE_TELEMETRY=1` did (0 vs 3 connections). Now set by default.

**Bugs found by the new tests:** `normalize_source` produced `document_id == "None"` for a missing id (fixed before commit).

**Mistakes during the session (no lasting effect):** a `pkill -f` pattern matched its own shell and aborted a manual smoke run (re-run from a script file); uninstalling `pyyaml` from the dev venv briefly broke `langchain-core` (it is a real dependency in the lock; venv re-synced from the lockfile).

**Outstanding:** live-provider verification (incl. Gemini and `max_completion_tokens`), live arXiv, real embedding model, Docker build, CI run — all blocked by the environment; see TEST_REPORT §5 and the completion report.
