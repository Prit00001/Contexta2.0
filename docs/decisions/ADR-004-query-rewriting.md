# ADR-004: Query rewriting as flag-gated experimental conditions

| | |
|---|---|
| Status | Accepted (implementation); measured 2026-10-18 — see Results. Default stays `off` pending the owner's decision |
| Date | 2026-10-18 |
| Scope | Contexta 2.0, Phase 3 |
| Builds on | [Phase 3 research brief](../research/PHASE_3_QUERY_REWRITING_RESEARCH.md), ADR-002, ADR-003 |

## Context

The research brief proposes six conditions (§3.2) and five hypotheses (§3.3) about rewriting the
user's question before retrieval. The Phase 2 baseline measured the gap it targets: `paraphrase`
Recall@1 0.718 vs `single_fact` 0.923 on MiniLM (EVALUATION_REPORT §2). Nothing about rewriting
quality can be claimed until the conditions run against a real LLM on the evaluation set; this ADR
records how the conditions are built so that run is a configuration change, not new code.

## Decision

1. **One switch, five conditions.** `QUERY_REWRITE = off | single | multi | decompose | selective`
   (brief conditions 1–5), default `off`. Condition 6 (local vs hosted rewriter) is
   `REWRITE_PROVIDERS`, which gives the rewriter its own provider chain (e.g. `custom` = a local
   Ollama) while answers still come from `LLM_PROVIDERS`.
2. **A `rewrite` node in the existing LangGraph workflow**, not a separate pipeline. It exists only
   when the mode is not `off`, so the baseline graph and its trace are byte-for-byte what Phase 2
   measured. `single`/`multi`/`decompose`: `validate_request → rewrite → retrieve`. `selective`:
   `retrieve → (weak?) rewrite → retrieve`, at most once (`rewritten` flag in state).
3. **At most one LLM call per request.** The rewriter returns every query in one JSON object
   (`{"queries": [...]}`), validated and de-duplicated (`core/query_rewrite.py`). UniRAG's
   iterative, RAGAs-gated loop (brief §2.1) is out of scope: its cost per question is unbounded on a
   free tier.
4. **Keep the original query** in every mode except `single` (brief §2.4, the ACL 2026
   masking-vs-mitigating result): per-query results are reciprocal-rank fused; each source keeps the
   best retrieval score it had, so `MIN_SCORE` and the selective gap still compare like with like.
5. **Rewrites search the local index only.** Live arXiv is queried for the original question alone,
   so rewriting never multiplies calls to an external API.
6. **Fail open to the baseline.** A failed or unparseable rewrite leaves retrieval on the original
   question; the trace marks `rewrite: failed` and `diagnostics` says why.
7. **Visible.** `query_rewrites` in `/ask`, `/ask-pdf` and `/workspaces/{id}/ask` responses, the
   `rewrite` stage in `workflow` and in `/metrics`, and an "Also searched for" line in the UI (the
   brief's §5 mitigation for hallucinated query terms).
8. **Measurement tool ships with the feature.** `eval/run_eval.py --rewrite all` runs every
   condition on the evaluation set: per-type Recall@1/@5, MRR, nDCG@10; LLM calls per question;
   rewrite latency p50/p95; retrieval-level abstention on unanswerable questions; and the
   embedding-similarity delta toward the gold document (RQR's reward signal, brief §2.3, used only
   as a diagnostic). Condition `off` reproduces the baseline retrieval exactly (tested).

## Not decided yet (needs the measured run)

- Which mode, if any, becomes the default. Every hypothesis H1–H5 is still open.
- `REWRITE_GAP_SCORE` (default 0.35) is a placeholder: the selective trigger is uncalibrated. On hash
  embeddings it fires on ~88% of questions (plumbing run below), so it must be set per backend from
  the `--rewrite selective` results, the same way `MIN_SCORE` was.
- Local model choice for condition 6 (brief §4 protocol: quality, JSON reliability, latency, memory).

## What has been run

Plumbing only: `--rewrite all` against `scripts/fake_llm_server.py` (which "rewrites" by stripping
stopwords and splitting on "and"/"vs") with hash embeddings. It confirms every condition runs, makes
the expected number of calls (0 / 1 / 1 / 1 / ≤1 per question) and that `off` matches the baseline.
**Its quality numbers say nothing about real rewriting and are deliberately not reported.**

## Results (2026-10-18, real LLM)

Rewriter Groq `openai/gpt-oss-120b`, MiniLM embeddings, hybrid retriever, 143 questions, 0 failed
or rate-limited rewrites. Full tables: EVALUATION_REPORT §5f. Provisional: the question set is
LLM-drafted and unreviewed.

| | Verdict | Evidence (overall unless stated) |
|---|---|---|
| **H1** single rewrite helps paraphrase, can hurt single-fact | **Half supported** | paraphrase R@1 0.718 → 0.821, R@5 0.949 → 0.974; single-fact *improved* (R@1 0.923 → 1.000), no over-rewriting harm seen |
| **H2** multi + fusion dominates single | **Not supported** on clean data | multi R@1 0.747 vs single 0.779; but under decoys multi is far more robust (decoy win 0.355 vs off 0.918) because the original query stays in the fusion |
| **H3** only decompose materially improves comparison | **Not supported** | comparison R@5 is already 0.984 at baseline; every mode leaves it within ±0.01 (decompose 0.974), R@1 +0.03 for multi and decompose alike |
| **H4** selective gets ≥70% of the best gain with ≤40% of the calls | **Not supported at the default gap; nearly at 0.45** | gap 0.35 fires on 18% and gains R@1 +0.009 (13% of single's +0.068). Simulated from the measured rankings: gap 0.45 fires on 38% and reaches R@1 0.756 (+0.045, 66%) — more than `multi` rewriting everything (0.747) |
| **H5** a local 7–8B model is within a few points of a hosted one, but slower on CPU | **Partly supported** (2026-10-19, EVALUATION_REPORT §5h) | qwen2.5:7b via Ollama, CPU-only: best R@1 0.747 (`multi`) vs 0.779 hosted (`single`); with reranking 0.802 vs 0.811; 5 unusable outputs in 429 calls. ~10× slower (p50 6.4 s vs 0.6 s). qwen2.5:1.5b gives no gain and fails ~11% of `single`/`decompose` calls |

Decision against the PRD adoption rule (beats `off` overall, no type loses >0.02 R@5, no drop in
unanswerable abstention):

- **`decompose` passes**: R@1 0.711 → 0.765, R@5 0.977 → 0.992, MRR 0.908 → 0.948, comparison R@5
  −0.010, retrieval-level abstention unchanged. Cost: one LLM call, ~0.5 s p50.
- `single` gains more R@1 but lowers retrieval-level abstention (0.061 → 0.030); `multi` and
  `selective` (0.35) gain less.
- **Recommendation for the owner, default left `off`:** reranking (EVALUATION_REPORT §5d.2) buys a
  similar gain (R@1 0.783, R@5 0.991) for +0.4 s with no LLM call or provider quota, so enable that
  first; add `QUERY_REWRITE=decompose` if quota allows, or `selective` with `REWRITE_GAP_SCORE=0.45`
  as the cheaper option. The rerank + rewrite combination on clean data has not been measured.
- `REWRITE_GAP_SCORE` default stays 0.35 (owner decision); 0.45 is the measured suggestion.

**Decision (2026-10-19, owner approved the recommendation).** Combined runs (cached rewrites, `hybrid_rerank`):
rerank alone R@1 0.783 / R@5 0.991 / MRR 0.950; `decompose` + rerank 0.811 / 0.991 / 0.970; `single` +
rerank 0.783 / 0.996 / 0.962; `multi` + rerank 0.797 / 0.973 / 0.961. With reranking on, rewriting adds
at most +0.03 R@1 for one LLM call per question, so: **reranking on by default** (`RERANK_MODEL`),
**`QUERY_REWRITE` stays `off`** (opt-in), and `REWRITE_GAP_SCORE` defaults to the measured 0.45 for anyone
who enables `selective`.

## Consequences

- Default behaviour, latency and cost are unchanged (`off`).
- Turning a mode on costs one extra LLM call per uncached request (fewer for `selective`) and adds
  the rewrite latency before retrieval.
- Decoy subset and per-retriever reporting (brief §3.4) were added afterwards: `--decoys`,
  `--retrievers hybrid,hybrid_bm25,dense,keyword`. Offline baseline (hash embeddings, no rewriting)
  in EVALUATION_REPORT §5c: decoys outrank gold on 94–98% of questions for every retriever.
