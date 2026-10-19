# ADR-005: Prefer the curated library over live arXiv in ranking

| | |
|---|---|
| Status | Accepted |
| Date | 2026-10-19 |
| Scope | Contexta 2.0, retrieval on the default `/ask` path (library + arXiv) |
| Builds on | ADR-003 (BM25 bias against arXiv), EVALUATION_REPORT §5d.2 (reranking), §5g (this decision's data) |

## Context

The first measurement of the default path, library plus live arXiv (EVALUATION_REPORT §5g), showed
arXiv results displacing the curated library paper the question is about: R@1 0.711 → 0.603 (hybrid)
and 0.653 (reranked); comparison-question R@5 fell to 0.73 with reranking. Live arXiv returns many
recent papers that share the question's vocabulary — for "How does LoRA reduce trainable
parameters?" a dozen LoRA variants — and both the hybrid score and the cross-encoder rate some of them
above the original paper. Reranking also cost 1.0–1.6 s on this path because arXiv abstracts are long.

## Decision

1. **A ranking prior for library sources.** The curated library is small, hand-picked and canonical;
   live arXiv is broad and recent. A library source's *ranking key* gets a fixed bonus; its reported
   `score` and the `MIN_SCORE` check stay on the raw score, so the prior reorders but never admits a
   source the quality gate rejected — the same rule as BM25 fusion (ADR-003) and reranking.
2. **Applied where the final order is decided.** With the reranker on (default), the bonus is added
   to the cross-encoder score (`RERANK_LIBRARY_BOOST`, logit units, default 3). With it off, it is added
   to the hybrid score (`LIBRARY_BOOST`, default 0.1). Only one applies, so each default is exactly the
   configuration it was measured in.
3. **Score only the start of long passages when reranking** (`RERANK_MAX_CHARS`, default 500). Library
   passages are all shorter, so library-only behaviour is unchanged.
4. **BM25 fusion stays off.** It recovers library recall only by removing arXiv from the results
   (arXiv share of the top 5: 0.7%).

## Consequences

- Default path: R@1 0.603 → 0.765, R@5 0.867 → 0.944, MRR 0.798 → 0.934; arXiv still fills about half
  of the top 5, and all of it when the library has nothing relevant (MIN_SCORE filters the library).
- Rerank latency on this path ~1.24 s → ~0.5 s p95 (offline replay), 130–200 ms per question live.
- The boost values are fitted to an unreviewed, library-targeted question set; they favour finding the
  library paper. A deployment that wants more arXiv weight lowers them (EVALUATION_REPORT §5g has the
  curve).
- Not measured: whether the displaced arXiv papers would have produced *better answers*; that needs
  arXiv-aware gold labels or an answer-level evaluation on the arXiv path.
