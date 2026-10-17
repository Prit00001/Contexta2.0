# Evaluation Report — Phase 2

## 1. Dataset

`eval/dataset/questions.jsonl` — 143 questions against the 25-paper library (`data/documents.py`,
`doc_001`–`doc_025`): `single_fact`=39, `paraphrase`=39, `comparison`=32, `unanswerable`=33. All
`gold_document_ids` grep-verified against real document IDs. **All items are `reviewed: false`** —
LLM-drafted (by the same assistant doing this implementation, directly against the corpus), not yet
checked by a human. `eval/dataset/REVIEW_SHEET.md` is the review artifact; flip `reviewed` to `true`
per-row as they're checked. **Numbers below may be inflated** relative to a truly independent
question set, for exactly that reason — treat this baseline as provisional until reviewed.

`eval/dataset/arxiv_snapshot.jsonl` is an empty placeholder (`export.arxiv.org` unreachable from
every sandbox used so far — TEST_REPORT.md §5, B-2). No question in the dataset depends on it; all
`comparison`/`paraphrase` questions are scoped to the local library.

## 2. Baseline (Phase 1 retrieval, unchanged)

Real `fastembed` MiniLM embeddings (`sentence-transformers/all-MiniLM-L6-v2`) — not hash embeddings.
Command: `PYTHONPATH=. python eval/run_eval.py` (retrieval-only; no LLM calls, no cost). Raw output:
`eval/report.json`.

| Type | n | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|
| single_fact | 39 | 0.923 | 1.000 | 1.000 | 0.957 | 0.968 |
| paraphrase | 39 | 0.718 | 0.923 | 0.949 | 0.822 | 0.866 |
| comparison | 32 | 0.443 | 0.938 | 0.984 | 0.953 | 0.950 |
| **overall** | 110 | 0.711 | 0.955 | 0.977 | 0.908 | 0.927 |

(`unanswerable` questions, n=33, are excluded from retrieval scoring — there's no gold document to
rank against; they're scored separately once `--with-llm` runs, on abstention correctness.)

## 3. Reading these numbers correctly

- **`single_fact` is strong** (Recall@1 0.92, saturates by @3): the baseline retrieval does well on
  in-vocabulary, single-paper questions. Expected — this is the easy case.
- **`paraphrase` Recall@1 drops to 0.72**, a real ~20-point gap from `single_fact` at the same k. This
  is the vocabulary-mismatch problem the Phase 3 research brief hypothesized (§1) — now measured, not
  assumed. This is the strongest evidence in this report for a retrieval change (P2-06 hybrid
  dense+BM25) actually being justified, *if* P2-06 specifically targets paraphrase-type recall.
- **`comparison` Recall@1 = 0.44 is a metric ceiling effect, not a weakness.** Every `comparison`
  question has 2 gold documents; `recall@1 = |top_1 ∩ gold| / |gold|`, so the maximum possible score
  for any 2-gold question is 0.5 (only one document can rank first). The real signal is Recall@3=0.94
  and Recall@5=0.98 — both gold documents are found almost every time once k is large enough to hold
  two results. **Do not read `comparison` Recall@1 as "comparison retrieval is bad."** This caveat
  exists because an earlier, uncritical read of this exact number would have been wrong — worth
  stating explicitly per the phase's own rule about measuring carefully.

## 4. What this baseline does *not* yet justify

- **P2-02 (MIN_SCORE calibration):** needs a precision/recall curve across threshold values, not yet
  run — this eval's retrieval always ranks the full top-k regardless of `MIN_SCORE` (the harness uses
  `min_score=0.0` deliberately, to score the true ranking). A follow-up run sweeping `MIN_SCORE` is
  needed before touching the default.
- **P2-04 (claim-support checking), P2-07 (reranking), answer-quality metrics generally:** need
  `--with-llm`, which makes real (free-tier) API calls. Not run yet — flagging before spending any
  provider quota, per the phase's own rule to ask before paid/opt-in eval runs.
- **P2-08/P2-09 (chunking, Qdrant):** explicitly gated on corpus size growing past what FAISS handles
  trivially; 25 documents doesn't approach that, so these stay deferred regardless of this report.

## 5. Cost and latency

Not yet measured — requires `--with-llm` (see §4). Retrieval-only latency per question was not
separately reported in this run; `eval/run_eval.py` records per-question `latency_ms` in
`retrieval_detail` within `report.json` for a future pass.

## 6. Known limitations of this dataset

- Unreviewed (see §1) — treat as provisional.
- Same model drafted the questions and knows the corpus — risk of unconsciously phrasing questions
  that are easier for retrieval than a real user's question would be, despite the explicit
  instruction to use different vocabulary for `paraphrase` items.
- No arXiv-sourced questions (snapshot not fetched — B-2 blocked everywhere tried so far).
- 25-document corpus is small; retrieval metrics on a corpus this size don't necessarily predict
  behavior at realistic scale (this is also why Qdrant/chunking, P2-08/09, stay deferred — there's
  nothing here yet that would show a difference).
