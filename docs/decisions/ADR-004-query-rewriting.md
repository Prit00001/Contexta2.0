# ADR-004: Query rewriting as flag-gated experimental conditions

| | |
|---|---|
| Status | Accepted (implementation); **effect not yet measured with a real LLM** |
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

## Consequences

- Default behaviour, latency and cost are unchanged (`off`).
- Turning a mode on costs one extra LLM call per uncached request (fewer for `selective`) and adds
  the rewrite latency before retrieval.
- Decoy subset and per-retriever reporting (brief §3.4) were added afterwards: `--decoys`,
  `--retrievers hybrid,hybrid_bm25,dense,keyword`. Offline baseline (hash embeddings, no rewriting)
  in EVALUATION_REPORT §5c: decoys outrank gold on 94–98% of questions for every retriever.
