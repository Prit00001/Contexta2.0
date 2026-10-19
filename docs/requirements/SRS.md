# Contexta 2.0 — Software Requirements Specification (Phase 1)

Companion to [PRD.md](PRD.md). Requirement IDs `SR-*` refine the product requirements `FR-*`/`NFR-*`.

## 1. System overview

A single FastAPI process (plus an optional ingestion worker process for the workspace platform) that:

1. Embeds a question with fastembed (`sentence-transformers/all-MiniLM-L6-v2`, 384-d, ONNX).
2. Retrieves candidates from an in-memory FAISS `IndexFlatIP` over the bundled paper library and, optionally, live arXiv results; merges, de-duplicates and re-ranks them.
3. Runs a LangGraph workflow that generates an answer with an OpenAI-compatible LLM (via LangChain), validates the citations, and falls back to an extractive answer when no LLM succeeds.
4. Returns a structured, source-attributed response.

## 2. System requirements

| ID | Requirement | Traces to |
|---|---|---|
| SR-01 | Python 3.11+ (Docker image: 3.11; verified locally on 3.13) | FR-01 |
| SR-02 | All configuration read from environment variables (optionally a `.env` file) into a validated Pydantic `Settings` object at startup | FR-08 |
| SR-03 | Invalid values (non-numeric number, unknown boolean, out-of-range) abort startup with an error naming the variable but not its value | FR-08 |
| SR-04 | `REQUIRE_LLM=true` with no usable provider aborts startup | FR-08 |
| SR-05 | Embedding model, FAISS index and HTTP client are created once in the app lifespan and shared | FR-01, FR-04 |
| SR-06 | No module outside the adapter modules imports LangChain/LangGraph directly, except the workflow module that defines the graph | FR-14 |
| SR-07 | Postgres/pgvector, Redis and Supabase remain optional; the public research path works without them | FR-13 |

## 3. API requirements

### 3.1 `POST /ask` (FR-03)

Request (JSON, validated):

| Field | Type | Constraints | Default |
|---|---|---|---|
| `question` | string | 3–500 chars (whitespace is collapsed before processing) | required |
| `top_k` | int | 1–10 | 5 |
| `use_arxiv` | bool | | true |

Unknown fields are ignored (backward compatibility with the existing UI).

Response 200 — existing fields are kept unchanged; new fields are additive:

| Field | Description |
|---|---|
| `question`, `answer`, `sources[]`, `total_sources`, `model_used`, `provider`, `llm_used`, `cached`, `warnings[]`, `diagnostics[]`, `timings_ms` | Unchanged from baseline |
| `request_id` | Correlation ID, also returned as the `X-Request-ID` header |
| `outcome` | `answered` (LLM answer), `extractive_fallback` (no LLM succeeded), `insufficient_evidence` (nothing relevant retrieved) |
| `sources[].marker` | The `[n]` number that refers to this source in `answer` |
| `sources[].document_id`, `sources[].chunk_id` | Source document vs. the specific passage used |
| `citations[]` | `{marker, source_id, document_id, chunk_id, title, url}` for every marker that appears in the answer and refers to a retrieved source |
| `claims[]` | Answer split into sentences: `{text, markers[], status}` where `status` is `cited` or `uncited` |
| `citation_check` | `{verification: "marker_only", valid_markers, removed_markers[], uncited_claims}` |
| `workflow[]` | `{stage, status, duration_ms}` for each executed LangGraph node |

Errors: 422 validation (FastAPI default body), 429 rate limited (`Retry-After: 60`), 500 unexpected (generic message + `request_id`, never a traceback).

### 3.2 `POST /ask-pdf` (FR-12)
Multipart `question` (3–1000 chars), `file` (PDF, ≤ `MAX_PDF_MB`), `top_k`. Same response schema as `/ask`. 400 empty, 413 too large, 415 not a PDF, 422 unreadable or no text.

### 3.3 `GET /health` (FR-02)
Liveness. Always 200 while the process serves requests. Body keeps the baseline fields (`status`, `index_size`, `model`, `embedding_backend`, `llm_providers`, `arxiv_enabled`). Provider **names** only — never keys, URLs with credentials, or DSNs.

### 3.4 `GET /ready` (FR-02)
Readiness. Body `{status, checks{...}}` with checks `index`, `embeddings`, `llm`, `arxiv`, `database`.

| Status | HTTP | Condition |
|---|---|---|
| `ready` | 200 | All checks ok |
| `degraded` | 200 | Serving, but with reduced quality: embedding fell back to hash vectors, no LLM provider (extractive mode), or DB configured but unreachable |
| `not_ready` | 503 | Index empty or service not initialised |

### 3.5 Workspace endpoints (FR-13)
`/workspaces*` routes are unchanged in contract. Changes are limited to SR-30/SR-31.

### 3.6 OpenAPI (FR-09)
`/docs` and `/openapi.json` include request examples and documented 422/429 responses for `/ask`.

## 4. Integration requirements

| ID | Integration | Requirement |
|---|---|---|
| SR-10 | LLM providers | OpenAI-compatible `/chat/completions` through `langchain-openai` `ChatOpenAI`, one instance per provider, SDK retries disabled (the chain handles fallback), per-call timeout `LLM_TIMEOUT` |
| SR-11 | Provider errors | 429 → cooldown max(Retry-After, 20 s) ≤ 120 s; 401/403 → 600 s; 404 → 600 s with a model-ID hint; timeout/network/5xx/malformed/empty → 30 s; then try the next provider |
| SR-12 | arXiv | `export.arxiv.org/api/query`, ≥ 3 s between calls, 8 s HTTP timeout, overall budget `ARXIV_BUDGET_S`; XML parsed with `defusedxml`; entries missing title/summary/id are dropped |
| SR-13 | Embeddings | fastembed; on load failure fall back to deterministic hash embeddings and report `degraded` readiness |
| SR-14 | Postgres | Optional; pgvector + FTS; migrations via `scripts/migrate.py` |

## 5. Workflow requirements (FR-15)

| ID | Requirement |
|---|---|
| SR-20 | State is an explicit typed dict: question, parameters, candidates, sources, answer, provider info, citation report, attempts, error, trace |
| SR-21 | Nodes: `validate_request` → `retrieve` → `generate` → `validate_answer` → `finalize`; plus `insufficient_evidence` and `fallback` terminal paths |
| SR-22 | Conditional edges only after `validate_request` (invalid → finalize with error), `retrieve` (no sources → insufficient_evidence), `generate` (no LLM answer → fallback) and `validate_answer` (unusable answer and attempts < `LLM_MAX_ATTEMPTS` → generate again; otherwise fallback) |
| SR-23 | Generation attempts are bounded by `LLM_MAX_ATTEMPTS` (default 2, max 3); the graph also has a LangGraph recursion limit |
| SR-24 | "Unusable answer" = empty, or contains zero valid citation markers while sources exist |

## 6. Error-handling requirements

| ID | Requirement |
|---|---|
| SR-40 | External failures (arXiv, LLM) never produce a 5xx on `/ask`; they degrade to library-only retrieval or extractive answers, with a user-facing `warnings[]` entry and a developer-facing `diagnostics[]` entry |
| SR-41 | Unexpected exceptions return 500 `{"detail": "Internal server error", "request_id": ...}` and are logged with category `internal` |
| SR-42 | Error messages never include API keys, DSNs, file-system paths or stack traces |
| SR-43 | Failure categories logged: `validation`, `rate_limited`, `retrieval_unavailable`, `llm_unavailable`, `llm_invalid_output`, `insufficient_evidence`, `internal` |

## 7. Security requirements

| ID | Requirement |
|---|---|
| SR-30 | Uploaded filenames are reduced to a safe basename (no path separators, no `..`, no control characters, length-limited) before use in storage paths; storage backends reject paths that escape their root |
| SR-31 | Workspace JWT verification refuses to run (HTTP 503) when `SUPABASE_JWT_SECRET` is unset, independent of PyJWT version |
| SR-32 | Retrieved and uploaded text is delimited in the prompt and the system prompt instructs the model to treat it as untrusted data, never as instructions |
| SR-33 | No code, shell command or tool call is ever executed from model output or documents; the LLM has no tools |
| SR-34 | Input limits: question ≤ 500 chars (`/ask`), ≤ 1000 (`/ask-pdf`); PDF ≤ `MAX_PDF_MB`; ≤ `MAX_PDF_CHUNKS` chunks; LLM `max_tokens` 1024 |
| SR-35 | Source URLs returned to clients must be `http`/`https`; anything else is replaced by an empty string |
| SR-36 | `/ask` remains unauthenticated by design (public demo) and is rate limited per client IP. With `TRUST_PROXY_HEADERS=true` (default, for Render/HF/Vercel) the right-most `X-Forwarded-For` entry — the one appended by the platform's proxy — is used; client-supplied left-most entries are ignored, so the limit cannot be bypassed by sending a fake header. With `false`, the socket peer address is used |
| SR-37 | `.env` is git-ignored; `.env.example` contains placeholders only |

## 8. Performance expectations

| Item | Expectation |
|---|---|
| Local retrieval | < 50 ms for the 25-document library (exact FAISS search) |
| arXiv | Bounded by `ARXIV_BUDGET_S` (default 9 s) |
| LLM | Bounded by `LLM_TIMEOUT` (default 25 s) × attempts × providers; typical single call 1–5 s |
| Cache | Identical LLM-answered `/ask` requests served from a 1 h in-process cache |
| Memory | No torch; target < 512 MB RSS |

## 9. Logging and privacy (FR-10, NFR-08)
One JSON object per line when `LOG_FORMAT=json` (default `text` for local development). Each request logs `request_id`, method, path, status, `duration_ms`. Workflow stages log `stage`, `status`, `duration_ms`, `category`. The question is logged only as `question_len` and an 8-char SHA-256 prefix. API keys are never passed to the logger.
