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
