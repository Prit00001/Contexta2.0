# Contexta 2.0 — PRD: Phase 2

Every requirement below is linked to either a Stage A baseline metric
([EVALUATION_REPORT.md](../evaluation/EVALUATION_REPORT.md)) or an explicit, already-documented
qualitative gap from Phase 1 (its completion report / [PHASE_2_BACKLOG.md](../roadmap/PHASE_2_BACKLOG.md)).
Backlog items the baseline doesn't justify are listed as deferred, not silently dropped.

## 1. Regression tolerances (apply to every requirement below)

- No metric (Recall@1/3/5, MRR, nDCG@10) on any question type drops more than **0.02 absolute**
  versus the Stage A baseline.
- p95 latency of `/ask` without an LLM configured (retrieval-only path) does not increase more
  than **30%** versus baseline.
- All existing tests (188 as of Stage A) continue to pass; `ruff check`/`format --check` stay clean.
- **Caveat:** the baseline is provisional — `eval/dataset/questions.jsonl` is unreviewed
  (`reviewed: false` throughout). Tolerances will be re-checked once a human review pass lands
  (ADR-002), in case gold labels change.

## 2. Requirements

### Must

**P2-FR-01 — Calibrate `MIN_SCORE` per embedding backend** (backlog P2-02)
Baseline: not yet measured — `eval/run_eval.py` currently scores the full ranking at `min_score=0.0`
by design (ADR-002 §4), not a sweep. Requirement: produce a precision/recall curve across candidate
`MIN_SCORE` values for the configured embedding backend; pick and document a value; abstention
correctness on `unanswerable` questions must not regress (needs the `--with-llm` baseline first — see
P2-FR-05).

**P2-FR-02 — Fast-fail model loading** (backlog P2-17)
Baseline: offline startup is ~45s (fastembed retries the blocked model download before falling back
to hash embeddings) — `TEST_REPORT.md` §3.1, `DEVELOPMENT_LOG.md` Session 1. Requirement: a single
download attempt (or `local_files_only` when a cache already exists) before falling back; offline
startup < 5s; `/ready` continues reporting the fallback accurately. Test added.

**P2-FR-03 — Hybrid dense + BM25 retrieval** (backlog P2-06)
Baseline: `paraphrase` Recall@1 = 0.718 vs. `single_fact` Recall@1 = 0.923 at the same k — a real
~20-point gap, the clearest signal in the Stage A baseline (EVALUATION_REPORT.md §3). Target:
`paraphrase` Recall@1 ≥ 0.80, Recall@5 ≥ 0.98 (matching `single_fact`'s ceiling); `single_fact` and
`comparison` must not regress beyond tolerance (§1). Requirement: BM25 (`rank-bm25`) fused with the
existing FAISS+cosine ranking via Reciprocal Rank Fusion, behind a config flag defaulting **off**
until the measured improvement is confirmed and within tolerance. ADR-003.

### Should

**P2-FR-04 — Claim-support checking** (backlog P2-04)
Baseline: qualitative, not a regression metric — citation validation is marker-level only
(`verification: "marker_only"`; Phase 1 completion report §10 item 1, defect D-05 in
`DEVELOPMENT_LOG.md` Session 1). Requirement: a new graph node after `validate_answer`; per-claim
`supported` / `not_supported` / `unknown` from an NLI model or LLM judge; precision/recall of the
checker itself measured on a labelled sample of ≥100 claims; `verification` field reflects the method
actually used; flag-gated if it doesn't fit the 512MB default.

**P2-FR-05 — Answer-quality baseline** (prerequisite for P2-FR-01's abstention check and for P2-FR-04)
Requirement: run `eval/run_eval.py --with-llm` once (real, free-tier API calls against all three
providers) to measure citation precision, abstention correctness on `unanswerable`, outcome
distribution, and cost/latency, before P2-FR-01 and P2-FR-04 can be called complete. **Needs explicit
authorization to spend provider quota before running** — flagged per the phase's own rule, not
run yet.

**P2-FR-06 — Workspace Q&A on the LangGraph workflow** (backlog P2-10)
Baseline: qualitative — `services/workspace_qa.py` calls `Generator` directly, bypassing the graph
(`SYSTEM_DESIGN.md` §2; backlog P2-10). Requirement: `/workspaces/{id}/ask` returns the same
`outcome`/`citations`/`claims`/`citation_check` fields as `/ask`; existing DB and tenant-isolation
tests still pass unmodified.

**P2-FR-07 — Shared cache, rate limiting, and metrics** (backlog P2-11, P2-15)
Requirement: Redis-backed answer cache and `/ask` rate limiter (in-memory fallback kept, matching the
workspace limiter's existing pattern in `core/rate_limit.py`); a `GET /metrics` Prometheus endpoint
with per-stage and per-outcome counters/histograms; metrics contain no user content (question text,
answers). A two-instance test proves the cache/limiter is actually shared.

### Could (this phase — may slip to Phase 3 if time runs out)

- **P2-FR-08 — Evidence-first frontend** (backlog P2-05, P2-12): per §6 of the master brief. Only
  starts once P2-FR-04 exists — there's nothing evidence-specific to show otherwise.
- **P2-FR-09 — SSE streaming** (backlog P2-13).
- **P2-FR-10 — Code-quality tightening** (backlog P2-16): shrink the `ruff format` exclude list for
  files actually touched this phase; enable `I`/`B` rule sets if it doesn't require a mass reformat.

### Explicitly deferred, not in this phase

- **P2-07 (reranking):** not yet justified by this baseline. Revisit after P2-FR-03 ships — if hybrid
  retrieval closes the paraphrase gap, reranking may turn out unnecessary.
- **P2-08/P2-09 (chunking, Qdrant):** the 25-document corpus doesn't approach FAISS `IndexFlatIP`'s
  limits. Stays an eval-only exercise per the backlog's own acceptance criterion; no adoption without
  a corpus-size trigger.
- **P2-14 (conversation history):** needs a concrete use case and a privacy/storage design the user
  approves — an explicitly blocking decision per the master brief (§9), not one to default silently.

## 3. Non-functional requirements

- **P2-NFR-01 (memory):** the default configuration stays within 512MB. P2-FR-04 and P2-FR-08 must be
  flag-gated if a feature doesn't fit.
- **P2-NFR-02 (API contract):** every Phase 1 response field stays; every change in this PRD is
  additive, matching A-01 and the Phase 1 `/ask` contract.
- **P2-NFR-03 (test hygiene):** no network or paid calls in the default test suite; eval runs needing
  real models or keys stay opt-in (`--with-llm`), exactly as Stage A's harness already does.
- **P2-NFR-04 (secrets):** no secrets in code, logs, eval outputs, or commits — same standard Stage 0
  already enforced (the real-key test-isolation bug fixed there is the concrete precedent).

## 4. Open question for the user

**P2-FR-05 needs your go-ahead before it runs** — it makes real API calls against your configured
Groq/Gemini/OpenRouter keys (free tier, but real traffic, ~143 questions). Say go whenever you want
that run, independent of the rest of Stage C.
