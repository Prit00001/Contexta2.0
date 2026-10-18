# Configuration

Source of truth: `app/config.py` (`Settings`). All values come from environment variables, optionally loaded from a `.env` file in the working directory (`python-dotenv`). Empty values (`DATABASE_URL=`) count as unset.

**Validation.** Settings are parsed once at startup. An invalid value — non-numeric number, unknown boolean, out-of-range value, unknown provider name, impossible combination — raises `ConfigError` and the server does not start. The message names the variable(s) and never echoes the value. Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`.

## LLM providers

A provider is *configured* when its key is set (for `custom`: when both `LLM_BASE_URL` and `LLM_MODEL` are set). Configured providers are tried in `LLM_PROVIDERS` order; a failing provider is skipped for a cooldown (429: Retry-After clamped to 20–120 s; 401/403/404: 600 s; other errors: 30 s).

| Variable | Default | Required | Notes |
|---|---|---|---|
| `GROQ_API_KEY` | – | no | |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | no | gpt-oss models get `reasoning_effort: low` |
| `GEMINI_API_KEY` | – | no | Uses Gemini's OpenAI-compatible endpoint |
| `GEMINI_MODEL` | `gemini-3.5-flash` | no | |
| `OPENROUTER_API_KEY` | – | no | Sends `HTTP-Referer` = `APP_SITE_URL`, `X-Title: Contexta` |
| `OPENROUTER_MODEL` | `meta-llama/llama-3.3-70b-instruct:free` | no | Free model IDs change; check OpenRouter |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | – | no | `custom`: any OpenAI-compatible server (Ollama: `http://localhost:11434/v1`) |
| `LLM_PROVIDERS` | `groq,gemini,openrouter,custom` | no | Comma-separated subset/order; unknown names are an error |
| `LLM_TIMEOUT` | `25` | no | Seconds per provider call, (0, 120] |
| `LLM_MAX_ATTEMPTS` | `2` | no | Generation attempts per question in the workflow, 1–3 |
| `CLAIM_CHECK` | `off` | `llm`: after citation validation, check each cited claim against its cited text — verbatim quotes locally, the rest in one LLM-judge call through the provider chain. Adds `claims[].support` and claim counts to `citation_check`. Costs one extra LLM call per uncached answer; judge precision not yet measured |
| `REQUIRE_LLM` | `false` | no | `true`: refuse to start with no configured provider |

No provider configured → the app still answers (`outcome: extractive_fallback`) and `/ready` reports `degraded`.

## Retrieval and embeddings

| Variable | Default | Notes |
|---|---|---|
| `EMBED_BACKEND` | `fastembed` | `hash` = deterministic token hashing, no download — tests only |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Must be a fastembed-supported model |
| `FASTEMBED_CACHE_PATH` | fastembed default | Model cache directory (Docker: `/app/.model_cache`, baked in) |
| `CACHE_DIR` | `.cache` (`/tmp/contexta-cache` on Vercel) | Library embedding cache (`embeddings.npz`) |
| `MIN_SCORE` | per backend | Hybrid-score cut for library/arXiv sources, [-1, 1]. Unset: `0.2` on fastembed, `0.075` on hash embeddings (incl. the fallback) — calibrated with `eval/run_eval.py --sweep-min-score` |
| `ARXIV_ENABLED` | `true` | Global switch; per request `use_arxiv` |
| `ARXIV_MAX_RESULTS` | `12` | 1–50 |
| `ARXIV_BUDGET_S` | `9` | Total arXiv time budget per request |
| `ARXIV_CACHE_TTL` | `3600` | Seconds |
| `ORT_DISABLE_TELEMETRY` | `1` (set by the app) | ONNX Runtime usage telemetry; `0` to opt in |

## API behaviour

| Variable | Default | Notes |
|---|---|---|
| `RATE_LIMIT_PER_MIN` | `20` | Per client IP on `/ask`, `/ask-pdf`; `0` disables. Shared across instances when `REDIS_URL` is set |
| `TRUST_PROXY_HEADERS` | `true` | Use the right-most `X-Forwarded-For` hop (the one your platform's proxy appends). Set `false` when clients connect directly |
| `ANSWER_CACHE_TTL` | `3600` | Seconds; only LLM answers (and empty results) are cached. In Redis (shared) when `REDIS_URL` is set, else per process |
| `ALLOWED_ORIGINS` | `*` | CORS, comma-separated |
| `APP_SITE_URL` | `http://localhost:8000` | Sent to OpenRouter |
| `MAX_PDF_MB` | `4` | `/ask-pdf` upload limit (Vercel rejects bodies > ~4.5 MB) |
| `MAX_PDF_CHUNKS` | `300` | Longer PDFs are truncated with a warning |

## Logging

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `text` | `json`: one object per line (Docker image default) |
| `METRICS_ENABLED` | `true` | `GET /metrics` (Prometheus). Counters/histograms by endpoint, outcome, stage, provider; no user content |

Logged per request: `request_id`, method, path, status, `duration_ms`, failure `category`. Per workflow stage: `stage`, `status`, `duration_ms`. Per answer: `outcome`, number of sources, provider, `question_len`, `question_sha8`. Never logged: question text, API keys, DSNs (a redaction filter is a backstop). LangSmith tracing is not enabled by the app; it would only activate if `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` were set, which sends prompts to LangSmith — leave unset unless intended.

## Workspace platform (optional)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | – | Postgres with pgvector. Unset → workspace routes return 503. Unreachable at startup → public API still starts, `/ready` shows `database: unreachable` |
| `SUPABASE_JWT_SECRET` | – | Required for workspace auth; unset → workspace JWT routes return 503 |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | – | Member lookup; Supabase storage |
| `STORAGE_BACKEND` | `local` | `local` or `supabase` (requires the two above) |
| `STORAGE_DIR` | `.storage` | Local backend root |
| `STORAGE_BUCKET` | `documents` | Supabase bucket |
| `MAX_UPLOAD_MB` | `20` | Workspace uploads |
| `REDIS_URL` | – | Shared answer cache, `/ask` and workspace rate limits across instances; falls back to in-memory if unset or unreachable |
| `WORKSPACE_RATE_LIMIT_PER_MIN` | `60` | |

## Safe local setup

1. `cp .env.example .env` — the example contains placeholders only.
2. Put keys only in `.env` (git-ignored, Docker-ignored). Never commit it; `git check-ignore .env` should print `.env`.
3. Prefer `REQUIRE_LLM=true` in deployments where extractive answers would be unacceptable.

## Startup and shutdown

- Start: `uvicorn app.main:app` (dev: `--reload`). Startup builds the FAISS index (embeddings cached in `CACHE_DIR`), creates one shared HTTP client, optionally a DB pool, and logs one `Ready:` line with embedding backend, providers and DB state.
- Shutdown: SIGINT/SIGTERM; the lifespan closes the HTTP client and DB pool. No state needs flushing (caches are in memory).

## Troubleshooting

| Message / symptom | Meaning |
|---|---|
| `ConfigError: Invalid configuration - LLM_TIMEOUT: Input should be a valid number…` | Fix the named variable |
| `REQUIRE_LLM=true but no LLM provider is configured` | Add a key or unset `REQUIRE_LLM` |
| Startup log `embeddings=hash (FALLBACK)` | Embedding model unavailable — retrieval quality reduced |
| `diagnostics`: `model 'X' not found (HTTP 404); check GROQ_MODEL` | Model ID retired — update the named variable |
| `diagnostics`: `API key rejected` | Wrong/expired key (provider skipped for 10 min) |
