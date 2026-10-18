# ADR-003: Hybrid BM25 retrieval (P2-FR-03)

| | |
|---|---|
| Status | Accepted, **off by default** |
| Date | 2026-10-18 |
| Scope | Contexta 2.0, Phase 2 |

## Context

Baseline (ADR-002/EVALUATION_REPORT.md): paraphrase Recall@1 = 0.718 vs single_fact 0.923 - a real
vocabulary-mismatch gap. P2-FR-03 proposed BM25 (`rank-bm25`) fused with the existing dense+keyword
ranking via Reciprocal Rank Fusion (RRF), to test whether a sparse signal helps.

## Decision

1. **Build a BM25 index over the 25-doc local library always** (`core/retriever.py`) - cheap at this
   corpus size - but only *use* it when `HYBRID_BM25_ENABLED` is set (`core/lc_retriever.py`).
2. **BM25 only re-ranks candidates that already passed `min_score` on the dense ranking** - it never
   admits a candidate the dense signal rejected. A first implementation skipped `min_score` entirely
   on the fused path and broke `test_empty_retrieval_does_not_call_the_llm` (a query with zero real
   matches still returned 4 sources, because common-word BM25 overlap alone was enough to rank
   something). Caught by the existing test suite, not inspection - exactly why "run the suite" is
   step one of this phase's own discipline.
3. **Off by default**, despite a clean, strong result on local-only retrieval (below) - because the
   eval harness only measures `use_arxiv=False`, and `/ask`'s real default is `use_arxiv=True`.
   `self.library.bm25_search` only indexes the local library, so arXiv-sourced candidates get no
   BM25 contribution in RRF fusion while local candidates can - a structural bias toward local
   results that `test_request_to_answer_with_library_and_arxiv` caught directly (an arXiv source
   that should have appeared in the top results was pushed out once BM25 fusion was default-on).
   This was never measured, and the one test that exercises it showed a real problem.

## Measured result (local-only retrieval, `eval/run_eval.py --hybrid-bm25`)

| Type | Recall@1 Δ | Recall@5 Δ | nDCG@10 Δ |
|---|---|---|---|
| single_fact | +0.026 | 0.000 | +0.013 |
| paraphrase | **+0.077** | +0.051 | +0.055 |
| comparison | +0.016 | +0.016 | +0.010 |
| overall | +0.041 | +0.023 | +0.027 |

Every metric improved, zero regressions, negligible latency cost (p95 3.5ms → 3.6ms). This is
genuinely strong evidence for the local-only path - the flag is recommended **on** for any
deployment that doesn't mix in live arXiv results.

## Consequences

**Positive:** a real, measured, flag-gated improvement exists and is documented, with a clean
code-level separation (dense ranking still gates quality; BM25 only reorders).

**Negative / trade-offs:**
- The actual default production behavior (`use_arxiv=True`) has not been validated with BM25
  fusion on, and has one known structural bias against arXiv results. Enabling it by default would
  require either (a) building a comparable sparse index over arXiv candidates per-request (adds
  latency, complexity), or (b) re-measuring with `use_arxiv=True` in the eval harness and confirming
  no regression on arXiv-containing answers - neither done yet.
- `eval/run_eval.py`'s retrieval metrics still don't cover the arXiv-mixed path at all - a real gap
  in Stage A's harness, not just this feature. Worth a follow-up eval harness extension before this
  flag is reconsidered for the default.
