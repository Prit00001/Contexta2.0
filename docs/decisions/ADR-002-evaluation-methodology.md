# ADR-002: Phase 2 evaluation methodology

| | |
|---|---|
| Status | Accepted |
| Date | 2026-10-17 |
| Scope | Contexta 2.0, Phase 2, Stage A |

## Context

Phase 2's one rule: measure first, then change. That needs a real evaluation set and harness before
any retrieval/generation change, per the backlog's own ordering (P2-01 gates everything else).

## Decision

1. **143 questions, four types, local-corpus-only.** `single_fact` / `paraphrase` / `comparison` /
   `unanswerable`, ≥30 each, against the existing 25-paper library (`data/documents.py`). No
   arXiv-sourced questions yet — `export.arxiv.org` is unreachable from every sandbox tried so far
   (TEST_REPORT.md §5, B-2); shipping the dataset scoped to what's actually testable now beats
   blocking Stage A on a snapshot nobody here can fetch.
2. **LLM-drafted labels, explicitly marked unreviewed.** `questions.jsonl` ships with every item
   `"reviewed": false`, plus a standalone review sheet for a human pass. The same assistant doing the
   implementation drafted the questions against the real corpus — a real risk of inflated numbers,
   stated plainly in `EVALUATION_REPORT.md` rather than hidden.
3. **Retrieval metrics computed directly against `ResearchRetriever`, not through the full graph.**
   Recall@1/3/5, MRR, nDCG@10 need no LLM; running them through `core/lc_retriever.py` directly keeps
   the default eval run free, fast, and deterministic (modulo the embedding backend). Answer-quality
   metrics (citation precision, abstention, cost/latency) go through the real graph
   (`services/research_graph.py`) and are gated behind `--with-llm`, since they cost real API calls.
4. **`min_score=0.0` inside the eval harness**, not the configured default (`0.2`). The harness scores
   the *true ranking* independent of the cutoff, so `MIN_SCORE` calibration (P2-02) can be done as a
   threshold sweep over already-collected rankings, rather than needing a separate eval run per
   candidate threshold.
5. **Real embeddings for the recorded baseline, never hash.** `fastembed`/MiniLM was confirmed
   downloadable in this sandbox (TEST_REPORT.md §5, B-1) via a GCS fallback URL even though
   `huggingface.co` itself is blocked. Hash embeddings remain available (`EMBED_BACKEND=hash`) for a
   fast dev loop, but are never reported as if they were the baseline.
6. **Report format: `report.json` (full detail, gitignored) + `report.md` (summary) per run, hand-
   curated into `EVALUATION_REPORT.md` for the record.** The harness's raw output isn't the document
   of record — it's regenerable; the narrated report with caveats (e.g. the comparison-type Recall@1
   ceiling effect) is.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Wait for arXiv access before building anything | Blocks Stage A indefinitely on an external dependency outside this project's control; the local-corpus-only dataset is still a real, useful baseline |
| Have the harness call the full LangGraph workflow for retrieval metrics too | Would make even the free, offline retrieval metrics require an LLM key — defeats the "deterministic, offline by default" requirement |
| Hand-write all 143 questions without LLM drafting | More defensible labels, much slower; the explicit `reviewed: false` + review sheet gets most of the trustworthiness at a fraction of the time, as long as the review actually happens before the numbers are treated as final |
| Score recall against `MIN_SCORE`-filtered results directly | Couples retrieval quality measurement to a tunable threshold that P2-02 is specifically trying to calibrate using this same data — circular |

## Consequences

**Positive:** Stage A unblocked without arXiv; baseline uses real embeddings, not a hash-embedding
stand-in; the comparison-type Recall@1 ceiling effect is caught and documented instead of silently
misread as a weakness.

**Negative / trade-offs:**
- No arXiv-sourced questions means paraphrase/comparison coverage is bounded by what the 25-paper
  library naturally supports — some real vocabulary-mismatch or multi-paper scenarios a live corpus
  would surface aren't represented yet.
- Unreviewed labels mean every number in `EVALUATION_REPORT.md` §2 is provisional until a human pass
  happens; before/after comparisons for Stage C changes should be re-checked once review lands, in
  case some gold labels move.
