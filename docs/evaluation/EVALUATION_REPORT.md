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

## 5a. P2-FR-03 — hybrid BM25 retrieval (2026-10-18)

Flag-gated (`HYBRID_BM25_ENABLED`, **off by default**) - see [ADR-003](../decisions/ADR-003-hybrid-retrieval.md)
for the full before/after table and why it isn't the default despite a clean local-only result
(the eval harness doesn't cover the arXiv-mixed path, which is the real default behavior, and the
one test that does cover it caught a structural bias against arXiv results).

## 5b. P2-FR-01 — `MIN_SCORE` calibration (2026-10-18)

Command: `PYTHONPATH=. python eval/run_eval.py --sweep-min-score` (offline, no LLM). Each question is
retrieved once at `min_score=0`; every candidate threshold is then applied to the collected rankings
(ADR-002 §4). Abstention here is **retrieval-level**: when nothing clears `MIN_SCORE` the workflow
answers `insufficient_evidence` without an LLM call, so it's measurable offline. LLM-level abstention
(sources retrieved, model declines) still needs `--with-llm` (P2-FR-05).

Selection rule (in the harness): the threshold with the highest abstention on the 33 `unanswerable`
questions among those whose Recall@5 stays within 0.02 of `min_score=0` (PRD §1 tolerance) **and**
that leave no answerable question without sources.

**Hash embeddings** (`EMBED_BACKEND=hash`, also what runs when fastembed fails to load), excerpt:

| MIN_SCORE | Recall@5 | Precision@5 | False abstention (answerable) | Abstention (unanswerable) |
|---|---|---|---|---|
| 0.0 | 0.920 | 0.236 | 0.000 | 0.000 |
| 0.05 | 0.920 | 0.247 | 0.000 | 0.000 |
| **0.075** | **0.915** | **0.291** | **0.000** | **0.030** |
| 0.1 | 0.888 | 0.365 | 0.009 | 0.182 |
| 0.15 | 0.783 | 0.581 | 0.073 | 0.515 |
| 0.2 (old default) | 0.608 | 0.773 | **0.264** | 0.758 |
| 0.3 | 0.323 | 0.983 | 0.655 | 0.970 |

- The old default 0.2 left **26% of answerable questions with zero sources** on hash embeddings — the
  Phase 1 "LoRA question returns 0 sources" observation, now measured.
- Hash scores separate answerable from unanswerable questions poorly: any threshold that abstains on
  a meaningful share of unanswerable questions also drops answerable ones. 0.075 is chosen for recall;
  on hash embeddings, abstention is left mostly to the LLM. New default: `DEFAULT_MIN_SCORE["hash"] =
  0.075` (`app/config.py`), used whenever `MIN_SCORE` is unset and the *effective* backend is hash.
- **MiniLM (fastembed) is not yet swept**: the model can't be downloaded from this sandbox
  (huggingface.co and the GCS mirror both return 403). Its default stays 0.2 until the same command
  runs where the model loads; the harness prints the recommended value and writes
  `eval/min_score_sweep_fastembed.json`.

## 5c. Phase 3 — retriever comparison and decoy check, no rewriting (2026-10-18)

Command: `PYTHONPATH=. python eval/run_eval.py --rewrite off --retrievers all [--decoys]` (offline,
no LLM; `eval/rewrite_eval.py`). **Hash embeddings only** — MiniLM isn't downloadable here, and hash
embeddings are essentially lexical, so these numbers must not be read as the production picture.

Without decoys (110 answerable questions, overall):

| Retriever | R@1 | R@5 | MRR | nDCG@10 |
|---|---|---|---|---|
| hybrid (/ask) | 0.665 | 0.920 | 0.861 | 0.867 |
| hybrid_bm25 | 0.702 | 0.952 | 0.899 | 0.903 |
| dense | 0.565 | 0.830 | 0.764 | 0.775 |
| keyword (BM25) | 0.752 | 0.977 | 0.929 | 0.933 |

With 220 decoys (two per answerable question, generated from its keywords: a short `literal` one and
a `repetition` one — brief §3.4; `make_decoys`):

| Retriever | R@5 | Decoy outranks all gold (overall) | literal / repetition |
|---|---|---|---|
| hybrid | 0.661 | 0.964 | 0.964 / 0.964 |
| hybrid_bm25 | 0.694 | 0.982 | 0.964 / 0.982 |
| dense | 0.535 | 0.955 | 0.936 / 0.955 |
| keyword | 0.682 | 0.982 | 0.973 / 0.982 |

Reading: on hash embeddings every retriever is almost completely captured by keyword-stuffed
decoys — expected, since all four signals are lexical here. The decoys are an adversarial upper bound
(they contain exactly the question's keywords). What this sets up, once MiniLM and a real rewriter are
available: (1) whether dense MiniLM resists literal/repetition decoys where hash cannot, and (2) the
brief's masking-vs-mitigating question — does `--rewrite multi|decompose --decoys` lower the decoy
win rate, or only shift scores? Without decoys, BM25 alone beats the hybrid on hash embeddings;
ADR-003's choice of hybrid was measured on MiniLM, so this is not a reason to change it.

## 5d. MiniLM results: calibration, reranking, retrievers, decoys (2026-10-18)

First runs with the real `sentence-transformers/all-MiniLM-L6-v2` embeddings since the environment
gained HuggingFace access. All offline (no LLM). The plain baseline reproduced §2 exactly (overall
R@1 0.711, R@3 0.955, R@5 0.977, MRR 0.908, nDCG@10 0.927), so the runs are comparable.

### 5d.1 `MIN_SCORE` sweep on MiniLM (P2-FR-01)

`eval/run_eval.py --sweep-min-score`, same rule as §5b. Excerpt:

| MIN_SCORE | Recall@5 | Precision@5 | False abstention | Abstention (unanswerable) |
|---|---|---|---|---|
| 0.1 | 0.977 | 0.257 | 0.000 | 0.000 |
| 0.2 (old default) | 0.973 | 0.368 | 0.000 | 0.061 |
| **0.225** | **0.970** | **0.415** | **0.000** | **0.091** |
| 0.25 | 0.952 | 0.470 | 0.000 | 0.182 |
| 0.3 | 0.915 | 0.565 | 0.018 | 0.364 |
| 0.4 | 0.803 | 0.771 | 0.064 | 0.848 |

Recommended: **0.225** (highest unanswerable abstention while Recall@5 stays within 0.02 of the
unthresholded ranking and no answerable question is left without sources). It is a small step from
0.2: on MiniLM, retrieval scores separate unanswerable questions only weakly, so most abstention has
to come from the LLM (measured in §5e). 0.25 would abstain on twice as many unanswerable questions but
costs 0.025 Recall@5, just over the PRD tolerance.

### 5d.2 Cross-encoder reranking (P2-07)

`eval/run_eval.py --rerank` — `Xenova/ms-marco-MiniLM-L-6-v2` over the top 20 candidates:

| Type | R@1 (no rerank → rerank) | R@5 | MRR |
|---|---|---|---|
| single_fact | 0.923 → **0.974** | 1.000 → 1.000 | 0.957 → 0.987 |
| paraphrase | 0.718 → **0.846** | 0.949 → 0.974 | 0.822 → 0.886 |
| comparison | 0.443 → 0.474 | 0.984 → **1.000** | 0.953 → 0.984 |
| **overall** | **0.711 → 0.783** | **0.977 → 0.991** | **0.908 → 0.950** |

Retrieval p95 latency 12 ms → **429 ms** (CPU, 20 pairs per question). Reranking is the largest
measured improvement so far and closes most of the paraphrase gap that motivated Phase 2 (§3). It
breaks the PRD's retrieval-latency tolerance (+30%), so it stays **off by default**; against the
1–2 s an LLM answer takes, ~0.4 s may well be worth it — an owner decision. Unlike BM25 fusion
(ADR-003) it scores library and arXiv candidates the same way, so it has no structural arXiv bias.

### 5d.3 Retrievers and decoys on MiniLM (brief §3.4)

`--rewrite off --retrievers all [--decoys]`, overall, 110 answerable questions:

| Retriever | R@5 clean | R@5 with decoys | Decoy outranks all gold | literal / repetition |
|---|---|---|---|---|
| hybrid (/ask) | 0.977 | 0.724 | 0.918 | 0.918 / 0.882 |
| hybrid_bm25 | 1.000 | 0.838 | 0.927 | 0.927 / 0.927 |
| dense | 0.973 | 0.615 | 0.909 | 0.909 / 0.791 |
| keyword (BM25) | 0.977 | 0.682 | 0.982 | 0.973 / 0.982 |
| hybrid_rerank | 0.991 | 0.861 | **0.755** | 0.718 / 0.736 |

Real embeddings resist keyword-stuffed decoys barely better than hash embeddings (§5c: 0.96): a
decoy built from the question's own keywords still outranks the real paper for ~9 in 10 questions.
Cross-encoder reranking is the only signal that pushes back (decoy win 0.92 → 0.76; single_fact
0.82 → 0.51), and BM25 fusion recovers the most Recall@5. The decoys are an adversarial upper bound —
real corpora rarely contain abstracts that are nothing but the question's keywords — but the result
says Contexta's ranking is driven largely by lexical overlap, which matters as the corpus grows.

## 5e. Real-LLM answer quality — Groq gpt-oss (2026-10-18)

`CLAIM_CHECK=llm python eval/run_eval.py --with-llm --pace 22` — all 143 questions through the full
LangGraph workflow, MiniLM embeddings, `MIN_SCORE` 0.2, library only (no arXiv). No rate-limit
errors in this run. **Provisional**: the question set is LLM-drafted and unreviewed (§1).

### Before the abstention fix (run 1)

| Type | n | Outcomes | Citation precision |
|---|---|---|---|
| single_fact | 39 | answered 39 | 0.987 |
| paraphrase | 39 | answered 39 | 0.722 |
| comparison | 32 | answered 32 | 0.969 |
| unanswerable | 33 | extractive_fallback 28, answered 3, insufficient_evidence 2 | — |

- All 110 answerable questions were answered by the LLM; 108 of 143 needed one generation attempt.
  Latency p50 1.33 s, p95 1.93 s.
- Citation precision (cited source is a gold source) is high for single-fact and comparison
  questions; **paraphrase questions are the weak spot (0.72)** — the same type with the weakest
  retrieval (§2), so the model cites the wrong paper when retrieval ranks it first.
- **Abstention was broken: 2 of 33 unanswerable questions (0.06).** The model did abstain — e.g. on
  "What is the architecture of a GAN?": *"none of the provided sources contain information about
  Generative Adversarial Networks"* — but an uncited answer failed citation validation, was
  regenerated, and ended as an extractive fallback quoting unrelated papers (28 of 33). Fixed in
  `core/abstention.py` (commit d8846a4); the rerun is below.

### After the fixes (run 2, `openai/gpt-oss-120b`)

The gpt-oss-20b daily token cap (200K/day on Groq's free tier) was exhausted by the first runs, so the
fixes were verified with `GROQ_MODEL=openai/gpt-oss-120b` on a fixed sample
(`--sample "single_fact:3,paraphrase:3,comparison:3,unanswerable:33"`). Different model from run 1 —
compare outcomes, not absolute quality. 0 rate-limited items.

| | Run 1 (20b, before) | Run 2 (120b, after) | **Run 3 (20b, after — like for like)** |
|---|---|---|---|
| Unanswerable correctly abstained | 2 / 33 (0.06) | 31 / 33 (0.94) | **31 / 33 (0.94)** — 29 by the LLM, 2 at retrieval |
| Unanswerable shown as extractive quotes | 28 / 33 | 1 / 33 | **0 / 33** |
| Answerable answered (sample of 9) | 9 / 9 | 9 / 9, precision 1.00 | **9 / 9, precision 1.00, all first attempt** |

Run 3 (2026-10-19, after the gpt-oss-20b daily quota reset) repeats run 1's model and settings
(`RERANK_MODEL=none`, `MIN_SCORE=0.2`, no claim check) on the same sample, so the fixes are the only
difference. 0 rate-limited items.

Run 2 first exposed a second bug: gpt-oss-120b cites as `【1】`, which the marker parser didn't
recognise, so 2 of 9 correctly cited answers were rejected as uncited. Fixed in `core/citations.py`
(full-width brackets normalised); the 9-question rerun above is after that fix.

### Claim-support checker on the same answers

250 cited claims (`docs/evaluation/claims_to_label.csv`): 78 `supported` by verbatim quote, 151
`supported` and 18 `not_supported` by the LLM judge, 3 `unknown`. Spot checks of the 18 look right —
the model adding details the cited abstract doesn't contain (e.g. Word2Vec's negative sampling,
ColBERT's MaxSim summation). **The checker's accuracy is not measured until the CSV is human-labelled**
(`--score-claims`).

## 5f. Phase 3 — query rewriting with a real LLM (2026-10-18)

`python eval/run_eval.py --rewrite all --rewrite-cache …` with `GROQ_MODEL=openai/gpt-oss-120b`
(`reasoning_effort=low`), MiniLM, hybrid retriever, all 143 questions. 0 rewrite failures, 0
rate-limited items. (An earlier gpt-oss-20b attempt is discarded: rate-limit cooldowns made most
rewrites fail instantly — see the harness fix in commit 8253385.)

| Condition | single_fact R@1 / R@5 | paraphrase R@1 / R@5 | comparison R@1 / R@5 | **overall** R@1 / R@5 / MRR | LLM calls/q | rewrite p50 | unanswerable abstain* |
|---|---|---|---|---|---|---|---|
| off | 0.923 / 1.000 | 0.718 / 0.949 | 0.443 / 0.984 | 0.711 / 0.977 / 0.908 | 0 | — | 0.061 |
| single | **1.000** / 1.000 | **0.821** / 0.974 | 0.458 / 0.984 | **0.779** / 0.986 / **0.949** | 1 | 519 ms | 0.030 |
| multi | 0.974 / 1.000 | 0.744 / 0.949 | 0.474 / 0.984 | 0.747 / 0.977 / 0.931 | 1 | 590 ms | 0.000 |
| decompose | 0.949 / 1.000 | **0.821 / 1.000** | 0.474 / 0.974 | 0.765 / **0.992** / 0.948 | 1 | 502 ms | 0.061 |
| selective (gap 0.35) | 0.949 / 1.000 | 0.718 / 0.949 | 0.443 / 0.984 | 0.720 / 0.977 / 0.914 | 0.18 | 593 ms | 0.000 |

\* retrieval-level: nothing clears `MIN_SCORE` (0.2 in this run). LLM-level abstention (§5e) is what
users mostly see; this column only shows whether rewriting pulls in sources for questions the library
can't answer.

**With decoys** (`--rewrite off,multi,decompose --retrievers all --decoys`, overall):

| Condition | hybrid R@5 / decoy win | hybrid_rerank R@5 / decoy win | keyword R@5 / decoy win |
|---|---|---|---|
| off | 0.724 / 0.918 | 0.861 / 0.755 | 0.682 / 0.982 |
| multi | 0.877 / **0.355** | **0.923 / 0.182** | 0.864 / 0.482 |
| decompose | 0.841 / 0.745 | 0.879 / 0.536 | 0.794 / 0.755 |

Reading:
- Rewriting helps most where the baseline is weakest — paraphrase R@1 +0.10 with `single` or
  `decompose` — and did not hurt single-fact questions.
- `decompose` is the only mode that meets the PRD adoption rule (beats `off` overall, no type loses
  >0.02 R@5, no drop in unanswerable abstention). `single` gains more R@1 but halves retrieval-level
  abstention; `multi` gains less.
- Comparison questions were already at R@5 0.98: no mode moves them materially.
- `selective` at the default gap 0.35 fires on 18% of questions and captures little of the gain;
  MiniLM top scores are mostly above 0.35 even when the top document is wrong.
- Decoys: much of `multi`'s robustness is by construction — the decoys are built from the original
  question's keywords and the rewrites use other words — so this shows rewriting *escapes* a
  keyword trap tied to the user's wording; it doesn't show the retriever's lexical bias is fixed
  (the brief's "masking vs mitigating", §2.4). The cross-encoder is the component that actually
  scores decoys lower in every condition.
- Cost: one extra LLM call (~0.5 s) per uncached question. Reranking alone (§5d.2: R@1 0.783,
  R@5 0.991, +0.4 s, no LLM call or quota) gets about the same gain as `decompose`; their
  combination on clean data was not measured.

## 6. Known limitations of this dataset

- Unreviewed (see §1) — treat as provisional.
- Same model drafted the questions and knows the corpus — risk of unconsciously phrasing questions
  that are easier for retrieval than a real user's question would be, despite the explicit
  instruction to use different vocabulary for `paraphrase` items.
- No arXiv-sourced questions (snapshot not fetched — B-2 blocked everywhere tried so far).
- 25-document corpus is small; retrieval metrics on a corpus this size don't necessarily predict
  behavior at realistic scale (this is also why Qdrant/chunking, P2-08/09, stay deferred — there's
  nothing here yet that would show a difference).
