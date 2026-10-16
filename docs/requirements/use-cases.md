# Contexta 2.0 — Use Cases (Phase 1)

## UC-01 Ask a research question
**Actor:** end user (web UI or API client). **Requirements:** FR-03–FR-07, FR-15.

**Preconditions:** server running; paper library indexed.

**Normal flow**
1. User submits `question` (and optionally `top_k`, `use_arxiv`) to `POST /ask`.
2. System validates the request.
3. System embeds the question and retrieves candidates from the local library and, if enabled, arXiv.
4. System merges, de-duplicates and ranks candidates; keeps the top `top_k` above `MIN_SCORE`.
5. System asks the first available LLM provider to answer using only the numbered sources.
6. System validates the answer: removes markers that refer to no retrieved source, splits the answer into claims and marks each `cited` or `uncited`.
7. System returns the answer, sources, citations, claims, timings, `request_id` and `outcome=answered`.

**Alternative flows**
- A1 *Cached*: an identical question answered by an LLM within `ANSWER_CACHE_TTL` → cached response, `cached=true`.
- A2 *arXiv unavailable* (timeout, HTTP error, malformed XML, budget exceeded): continue with library results; add warning "Live arXiv search was unavailable…".
- A3 *arXiv returns nothing*: continue with library results, no warning.
- A4 *Provider fails* (429, 401/403, 404, 5xx, timeout, malformed, empty): put provider on cooldown, try the next one.
- A5 *Answer unusable* (no valid citation): regenerate once (bounded by `LLM_MAX_ATTEMPTS`).
- A6 *No provider succeeds / none configured*: extractive answer from the top passages with citations; `outcome=extractive_fallback`, `llm_used=false`.

**Failure cases**
- F1 Invalid input (too short/long, `top_k` out of range, wrong types) → 422.
- F2 Rate limit exceeded → 429 with `Retry-After: 60`.
- F3 Nothing relevant retrieved → 200, `outcome=insufficient_evidence`, no sources, no LLM call, answer says the sources don't cover the question.
- F4 Unexpected server error → 500 with generic message and `request_id`.

## UC-02 Ask about an uploaded PDF
**Actor:** end user. **Requirements:** FR-12, FR-05, FR-06.

**Preconditions:** PDF ≤ `MAX_PDF_MB`, contains extractable text.

**Normal flow:** user uploads PDF + question to `POST /ask-pdf` → text extracted per page and chunked → chunks embedded (cached per instance) → top chunks ranked → same generation and citation validation as UC-01 → sources carry page numbers and `chunk_id`s.

**Failure cases:** empty file → 400; too large → 413; not a PDF → 415; encrypted/corrupt or no text → 422. Over `MAX_PDF_CHUNKS` → answered from the first chunks with a warning.

## UC-03 Check liveness and readiness
**Actor:** operator, platform health checker. **Requirements:** FR-02.

1. Platform polls `GET /health` → 200 while the process is alive.
2. Operator calls `GET /ready` → `ready`, `degraded` (with the reason per check) or `not_ready` (503).

**Alternative:** embedding model couldn't be loaded → `degraded`, `checks.embeddings.ok=false`, detail "hash fallback". No LLM key → `degraded`, detail "extractive mode".

## UC-04 Workspace document Q&A (existing platform, preserved)
**Actor:** authenticated workspace member. **Requirements:** FR-13.

**Preconditions:** `DATABASE_URL`, `SUPABASE_JWT_SECRET` set; worker running.

**Normal flow:** create workspace → upload PDF/DOCX/TXT/MD → worker parses, chunks, embeds into pgvector → `POST /workspaces/{id}/ask` → hybrid (vector + FTS) retrieval → generation → citations with filename/page/section.

**Failure cases:** missing/invalid token → 401; not a member or insufficient role → 403; DB not configured → 503; JWT secret not configured → 503; unsupported type or mismatched magic bytes → 400; oversize → 413; filename with path components → stored under its sanitised basename.

## UC-05 Configure providers
**Actor:** developer/operator. **Requirements:** FR-08.

1. Copy `.env.example` to `.env`; set one or more provider keys; optionally `LLM_PROVIDERS`, model IDs.
2. Start the server. Startup log lists provider **names** in chain order.

**Failure cases:** a number/boolean variable has an invalid value → startup fails naming the variable. `REQUIRE_LLM=true` and no provider configured → startup fails. A wrong key → provider gets 401 at request time, 600 s cooldown, diagnostic "API key rejected"; the request still answers via the next provider or extractive fallback.
