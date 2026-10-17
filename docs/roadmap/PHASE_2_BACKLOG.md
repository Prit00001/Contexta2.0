# Phase 2 Backlog — RAG and Frontend Refinement

Not implemented in Phase 1. Ordered by evidence: items backed by something Phase 1 observed come first. Rule for Phase 2: **build the evaluation set (P2-01) before changing retrieval**, so every later item is justified by a measured shortcoming rather than assumed.

Priority: **P0** do first · **P1** next · **P2** when justified by P2-01 results.

| ID | Item | Evidence from Phase 1 | Priority | Acceptance idea |
|---|---|---|---|---|
| P2-01 | Retrieval evaluation set + metrics harness | Phase 1 has functional tests but no quality numbers; ranking equivalence was checked against the baseline, not against ground truth | P0 | **Done** (2026-10-17) — 143 questions (`reviewed: false`, pending human review), `eval/run_eval.py` / `make eval`; real-embedding baseline recorded in [EVALUATION_REPORT.md](../evaluation/EVALUATION_REPORT.md), methodology in [ADR-002](../decisions/ADR-002-evaluation-methodology.md) |
| P2-02 | Calibrate `MIN_SCORE` per embedding backend | With hash embeddings the default 0.2 left a LoRA question with 0 sources (baseline audit); threshold was tuned for MiniLM informally | P0 | Threshold chosen from P2-01 precision/recall curve; documented |
| P2-03 | Verify live providers and token parameter | `langchain-openai` sends `max_completion_tokens`; Gemini compat endpoint not verified from the build sandbox | P0 | **Done** (2026-10-17, TEST_REPORT §5) — Groq/Gemini/OpenRouter all 13/13; `max_completion_tokens` accepted by Gemini; found+fixed a real bug (OpenRouter's default model was retired) |
| P2-04 | Claim support check (entailment) | Phase 1 validates markers only (`verification: marker_only`) | P1 | Per-claim `supported` / `not_supported` / `unknown` from an NLI model or LLM judge; precision measured on a labelled sample |
| P2-05 | Evidence inspection UX | API returns `claims[]`, `citations[]`, `citation_check` but the UI ignores them | P1 | UI marks uncited claims, shows removed markers, links each `[n]` to its excerpt |
| P2-06 | Hybrid dense + sparse retrieval for `/ask` | Current hybrid is cosine + keyword *overlap ratio*, not BM25; arXiv query is keyword OR/AND | P1 (if P2-01 shows lexical misses) | BM25 (e.g. `rank-bm25`) fused with FAISS via RRF; improvement on paraphrased/keyword question types |
| P2-07 | Reranking | No reranker; cross-encoder previously ruled out for 512 MB RAM | P2 | Small ONNX cross-encoder or LLM rerank on top-20; quality gain vs latency/RAM measured |
| P2-08 | Chunking + metadata filtering | Library entries are single abstracts (one chunk each); PDFs use ~900-char sentence chunks with 1-sentence overlap | P2 | Only when the corpus grows beyond abstracts: section-aware chunking, filters by year/source |
| P2-09 | Qdrant evaluation vs FAISS baseline | FAISS `IndexFlatIP` over 25 docs is exact and fast; no current need | P2 | Benchmark at 10k / 100k chunks: recall, latency, RAM, ops cost; adopt only if FAISS limits are hit |
| P2-10 | Move workspace Q&A onto the LangGraph workflow | `services/workspace_qa.py` calls `Generator` directly, so it lacks claims/citation_check/retry | P1 | `/workspaces/{id}/ask` returns the same citation fields; existing DB tests pass |
| P2-11 | Shared cache + rate limit across instances | Answer cache, PDF-vector cache and `/ask` limiter are per process | P1 (before scaling > 1 instance) | Redis-backed (Redis already used for workspace limits) |
| P2-12 | Polished React/TypeScript frontend | Current UI is a single static HTML file (works, escapes output) | P2 | Only after P2-05 defines what to show; keep API contract |
| P2-13 | Streaming answers | Not implemented; LLM latency dominates (`timings_ms.generate`) | P2 | SSE endpoint streaming tokens, citation validation on completion |
| P2-14 | Conversation history | Not implemented; each request independent | P2 | Only with a clear use case; requires storage + privacy review |
| P2-15 | Observability: metrics + tracing | JSON logs with stage durations exist; no metrics endpoint or traces | P1 | Prometheus counters/histograms per stage and outcome; optional OpenTelemetry / LangSmith (off by default) |
| P2-16 | Code-quality tightening | 26 legacy files excluded from `ruff format`; import sorting (`I`) off | P2 | Exclude list empty; `I` and `B` rule sets on |
| P2-17 | Model download resilience | fastembed retries the download for ~45 s at startup when offline before falling back | P1 | **Done** (2026-10-17) — `core/embeddings.py`: `local_files_only=True` when cached (0.45s, measured), `retries=1` otherwise; `tests/test_embeddings.py` |

## Out of scope for Phase 2
Query rewriting and local inference (Phase 3 — see the research brief), multi-agent workflows, authentication for the public `/ask` demo endpoint (product decision A-01).
