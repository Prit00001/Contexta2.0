# Changelog

All notable changes to Contexta 2.0. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — Techniques from recent papers, measured (2026-10-20)

### Added
- `SUFFICIENCY_CHECK=llm` (after *Sufficient Context*, ICLR 2025): before answering, judge whether the retrieved passages are enough; abstain and name what is missing when they clearly aren't. Measured on the default path: correct abstentions 7/20 → 12/20, one answerable question declined (a retrieval miss). Off by default (+1 model call).
- `MIN_SCORE_ARXIV`: a per-source relevance cutoff for arXiv (after *Beyond the Reranker*, 2026). Calibrated with LLM relevance labels: no cutoff separates useful arXiv papers from the rest, so it stays unset.
- Eval: `run_eval.py --with-llm --arxiv --rerank` replays `/ask`'s real default path; `eval/source_thresholds.py` calibrates per-source cutoffs.

### Measured, not changed
- Query expansion on top of the reranker (`QUERY_REWRITE=decompose`): R@1 0.783 → 0.811, R@5 unchanged at 0.991. Stays off (+1 model call for a ranking gain the model rarely sees).
- Finding: only ~6% of fetched arXiv papers help answer the question; the arXiv query is the next thing to fix (EVALUATION_REPORT §5k).

## [Unreleased] — Stronger hallucination checks (2026-10-20)

### Added
- Claim checks on by default (`CLAIM_CHECK=local`, no model call): a sentence stating a figure (175 billion, 40%, 2019) that none of its cited passages contain is marked `not_supported` (`support_method: "numbers"`, with `unsupported_numbers`). Model names (GPT-3, LLaMA-13B), small counts and number words are handled. `CLAIM_CHECK=llm` adds the LLM judge for the rest; `off` restores marker-only checks.
- Claim repair (`CLAIM_REPAIR`, on): a flagged answer is rewritten once with feedback naming the sentences and why; the rewrite is kept only if it has fewer unsupported claims, else the first answer stays with its flags. Streaming clients get a `reset` event with `"reason": "repair"`.
- The answer view names the missing figure on a flagged sentence and counts flagged sentences in the evidence line.
- `eval/claim_check_bench.py`: benchmark from 250 real claims plus wrong-source, wrong-number and flipped-direction variants.

## [Unreleased] — SEO checklist gaps (2026-10-20)

### Added
- Link-preview image (`/static/og.png`, 1200×630, drawn by `scripts/make_og_image.py`) on `/` and `/about`: `og:image` with size and alt text, `twitter:card` = `summary_large_image`.
- A real 404 page (status 404, `noindex`) for unknown addresses opened in a browser; API clients and deliberate endpoint 404s keep their JSON errors.
- Permanent (301) redirects: trailing slash (`/about/` → `/about`) and `www.` ↔ the host in `APP_SITE_URL`. GET/HEAD only.

### Fixed
- Workspace pages no longer carry a canonical link to `/` (it contradicted their `noindex`) and get the title "Workspace · Contexta" from the server.

## [Unreleased] — Vercel deployment (2026-10-20)

### Added
- `vercel.json`: FastAPI preset, installs the pinned `requirements.txt`, builds the React app and applies migrations at build time, 300 s functions, tests/docs/`node_modules` left out of the bundle, and a daily cron. `.vercelignore`, `[tool.vercel] entrypoint`. Guide: [VERCEL.md](docs/operations/VERCEL.md).
- `INGEST_INLINE` (default on Vercel): an upload is ingested inside its own request and returns `ready`.
- `GET /cron/maintenance` (enabled by `CRON_SECRET`): deletes expired conversations and ingests anything queued.
- On Vercel: storage dir in `/tmp` and a 4 MB upload limit by default.

## [Unreleased] — Search and AI answer engine visibility (2026-10-20)

### Added
- `/about`: server-rendered page with what Contexta is, how it works, pricing, the 25 library papers and an FAQ, with `WebApplication` and `FAQPage` structured data (JSON-LD). Most AI crawlers don't run JavaScript, so this is what they can read and quote.
- `/robots.txt` (private pages and `?q=` answer links excluded), `/sitemap.xml`, `/llms.txt` (a Markdown map of the site for language models). All use `APP_SITE_URL`, else the forwarded host.
- The app shell has a real title, description, canonical and Open Graph tags, structured data, and readable content before JavaScript loads. Workspace pages send `X-Robots-Tag: noindex`.
- `/?q=…` links open the search page and ask that question; asking a new question updates the link so it can be shared. Per-page titles, an About link in the nav, and HEAD support on public pages.
- GO_LIVE.md §5: after-deploy steps (Search Console, Bing Webmaster Tools, rich results check, keeping the free instance awake, getting mentioned, measuring).

## [Unreleased] — Go live: sign-up and Render setup (2026-10-20)

### Added
- Sign-up, email confirmation and password reset on `/workspace` (Supabase Auth): "Create an account", "Forgot password?", and a "Choose a new password" step after a reset link. Email links sign the user in and are removed from the URL; expired links say so.
- Tokens from Supabase's newer JWT signing keys (ES256/RS256) are verified against the project's JWKS; HS256 with `SUPABASE_JWT_SECRET` still works.
- `INGEST_IN_API=true`: ingestion runs inside the API process (Render's free plan has no background workers). The worker now reconnects when the database drops its connection.
- `MIGRATE_ON_START=true` (Docker): applies pending migrations before the server starts.
- `render.yaml` declares every setting for workspaces and billing; [GO_LIVE.md](docs/operations/GO_LIVE.md) walks through Supabase, Razorpay and Render, then a test-payment checklist.

### Fixed
- Adding a member by email asked Supabase's admin API for `?email=`, which it ignores, and took the first user returned: on a real Supabase project that could add the wrong person. It now uses the `filter` parameter and requires an exact email match.

## [Unreleased] — Workspace billing (2026-10-19)

### Added
- Billing for workspaces ([ADR-007](docs/decisions/ADR-007-billing.md)), off unless `BILLING_ENABLED=true`: one hour of active use free per workspace (idle time never counts), then a per-seat monthly Razorpay subscription. After the trial, asking and uploading return 402; everything stays readable; paper search stays free.
- `GET /workspaces/{id}/billing`, `POST /workspaces/{id}/activity` (trial heartbeat), `POST .../billing/subscribe`, `.../billing/verify` (Checkout signature), `.../billing/cancel`, and the signed Razorpay webhook `POST /billing/razorpay/webhook`. Member changes update the seat count from the next renewal.
- Frontend: a trial banner with minutes left, a Billing page with Razorpay Checkout, and paywall messages on ask and upload.
- Migration `0004_billing.sql`.

## [Unreleased] — React frontend (P2-12) (2026-10-19)

### Added
- `frontend/`: React 19 + TypeScript app (Vite 8, react-router 7, pnpm; no UI library). **Papers** (`/`): streaming cited answers, PDF upload, follow-ups, evidence flags. **Workspace** (`/workspace`): sign-in, workspace picker and creation, documents with live ingestion status, threads with saved conversations at their own URL (`/workspace/{id}/ask/{conversation}`), **Members** (add by email, change role, remove) and **API keys** (create, shown once with copy, revoke) for admins. Escaped by construction (React); an error boundary keeps a failing view from blanking the app.
- FastAPI serves `frontend/dist` at `/` and `/workspace/*` when built, else the original static pages.
- Dockerfile: Node build stage; CI: `frontend` job (typecheck, vitest, build) and the Docker smoke test checks the app is served.
- `GET /workspaces/{id}/members` now includes each member's `email`.

## [Unreleased] — Workspace UI (2026-10-19)

### Added
- `/workspace` page (`app/static/workspace.html`, no build step, same design tokens as the main page): sign in, pick or create a workspace, upload / delete documents with live ingestion status, ask questions with a sidebar of saved conversations (open, continue, delete; shows when a thread will be deleted). Role-aware: viewers can ask but not change documents. Linked from the main page footer.
- `GET /workspaces` (the signed-in user's workspaces and roles) and `GET /auth/config` (public sign-in settings for the page).
- `SUPABASE_ANON_KEY`: enables email + password sign-in on the page; without it the page accepts a pasted access token.

## [Unreleased] — Stored workspace conversations (P2-14) (2026-10-19)

### Added
- Server-side conversation history for workspace Q&A ([ADR-006](docs/decisions/ADR-006-conversation-history.md)), on the design the owner approved: visible only to the author (not other members, not admins), deleted 30 days after the last turn, full source passages stored per turn.
- `POST /workspaces/{id}/ask`: `save` starts a thread, `conversation_id` continues it (the server supplies the last 3 turns as history), `history` for stateless follow-ups as in `/ask`; responses carry `conversation_id` and `standalone_question`.
- `GET /workspaces/{id}/conversations`, `GET` / `DELETE /workspaces/{id}/conversations/{cid}`.
- Migration `0003_conversations.sql` (tables + author-only RLS), `HISTORY_RETENTION_DAYS` (default 30); expired threads are hidden on read and deleted hourly by the worker.

## [Unreleased] — Tracing (P2-15) (2026-10-19)

### Added
- OpenTelemetry tracing, off by default (`TRACING_ENABLED`, `core/tracing.py`): a `workflow.<stage>` span per research-graph node and an `llm.call` span per provider call, nested under FastAPI's server span (tagged with the request ID). Export over OTLP/HTTP with the standard `OTEL_*` variables; the SDK is optional (`requirements-otel.txt`, Docker `--build-arg WITH_OTEL=true`). No question, answer, source text or credentials in spans.
- `opentelemetry-api` is now a direct dependency (it was already installed via langsmith; the runtime lock only changes in comments).

### Changed
- Backlog: P2-11 (shared Redis cache and limiter) and P2-15 marked done.

## [Unreleased] — Node-by-node workflow check (2026-10-19)

### Added
- `tests/test_graph_routes.py`: 18 routes through the research graph with the exact (node, status) trace each must produce, plus a check that every node is reached. Four statuses (`invalid`, `empty`, `no_llm`, `unusable`) were previously untested; it also pins that claim-checking an extractive fallback makes no LLM call.
- `scripts/live_node_check.py`: drives every node and `/ask/stream` through the real API with a real LLM and live arXiv. 11/11 checks pass against Groq `gpt-oss-120b`.

## [Unreleased] — Library prior: answer-level check (2026-10-19)

### Added
- `eval/boost_compare.py`: answers the questions whose sources change between two `RERANK_LIBRARY_BOOST` values and has a judge model pick the better answer blind. EVALUATION_REPORT §5i: boost 3 vs 0 is 4 better / 3 worse / 33 ties on 40 questions, so the default stays.

## [Unreleased] — Phase 3 H5: local rewriter (2026-10-19)

### Added
- EVALUATION_REPORT §5h: Ollama qwen2.5:1.5b and qwen2.5:7b (CPU-only) vs hosted gpt-oss-120b as the query rewriter, all modes, plain and reranked retrieval. H5 partly supported; ADR-004 and the research brief updated. Phase 3 is complete.

## [Unreleased] — Library + arXiv path (2026-10-19)

### Added
- `scripts/fetch_arxiv_snapshot.py` and `eval/dataset/arxiv_snapshot.jsonl` (143 questions, 1,716 live arXiv papers); `eval/run_eval.py --arxiv` replays it, so `/ask`'s default path is measured for the first time.
- `RERANK_LIBRARY_BOOST` / `LIBRARY_BOOST` library prior and `RERANK_MAX_CHARS` ([ADR-005](docs/decisions/ADR-005-library-prior.md)).

### Changed
- Defaults `RERANK_LIBRARY_BOOST=3`, `LIBRARY_BOOST=0.1` (only without the reranker), `RERANK_MAX_CHARS=500`: default-path R@1 0.603 → 0.765, R@5 0.867 → 0.944; rerank p95 ~1.24 s → ~0.5 s.

## [Unreleased] — Defaults from the measured runs (2026-10-19)

### Changed
- **Reranking on by default** (`RERANK_MODEL=Xenova/ms-marco-MiniLM-L-6-v2`; `none` disables): R@1 0.711 → 0.783, R@5 0.977 → 0.991, +0.4 s per search, 411 MB peak RAM. The model is baked into the Docker image.
- `REWRITE_GAP_SCORE` default 0.35 → 0.45 (measured). `QUERY_REWRITE` stays `off`: on top of reranking it adds ≤ +0.03 R@1 for an LLM call per question.

## [Unreleased] — Real-LLM evaluation (2026-10-18)

### Fixed
- An LLM answer saying the sources don't cover the question was rejected as uncited, regenerated, and replaced by extractive quotes from unrelated papers (28 of 33 unanswerable eval questions). Now kept as `insufficient_evidence` (`core/abstention.py`); abstention 0.06 → 0.94.
- Full-width citation brackets (`【1】`, gpt-oss-120b) were not recognised as markers, so cited answers were rejected.
- Eval harness: provider cooldowns after a 429 made questions fail instantly; evals now wait and retry, and report items still rate-limited.

### Changed
- `MIN_SCORE` default on MiniLM 0.2 → 0.225 (measured sweep).

### Added
- `eval/run_eval.py`: `--pace`, `--rewrite-cache` (per model, saved per call), `--sample`, `hybrid_rerank` retriever kind.
- Measured results: EVALUATION_REPORT §5d–5f, ADR-004 Results; `docs/evaluation/claims_to_label.csv` for human labelling.

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
