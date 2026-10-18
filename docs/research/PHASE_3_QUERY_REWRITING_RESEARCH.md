# Phase 3 Research Brief — Query Rewriting for Contexta

| | |
|---|---|
| Status | Conditions 1–5 implemented and measured with Groq gpt-oss-120b (2026-10-18): H1 half supported, H2–H4 not supported as stated, H5 untested — [ADR-004 Results](../decisions/ADR-004-query-rewriting.md#results-2026-10-18-real-llm) |
| Date | 2026-10-16 |
| Method | Publication details verified on ACL Anthology / arXiv; papers read via their PDFs or HTML versions; summaries are paraphrased |

**How to read this document.** Each paper section has three parts: *What the paper reports* (the authors' claims, not re-verified by us), *Limitations* (stated by the authors, plus our reading where marked), and *Relevance to Contexta* (our hypotheses — untested). Nothing below claims a reproduction of any paper.

## 1. Why query rewriting, and why carefully

Contexta's retrieval (Phase 1) embeds the raw question with MiniLM and combines cosine similarity with keyword overlap; the arXiv query is a keyword OR/AND of up to five terms. Two failure patterns are likely and should be *measured* in Phase 2 before anything is built:

1. **Vocabulary mismatch** — a question phrased differently from the paper ("how to make LLM finetuning cheaper" vs. "low-rank adaptation").
2. **Multi-part questions** — "compare DPR and ColBERT on cost and accuracy" needs evidence about two papers; a single embedding may retrieve only one.

Rewriting can address both, but it adds LLM calls (latency, cost, a new failure point) and — per the ACL 2026 paper below — can *hide* retriever problems rather than fix them.

## 2. Papers

### 2.1 UniRAG (Findings of EMNLP 2025)

**Citation.** Gun Il Kim, Jong Wook Kim, Beakcheol Jang. *UniRAG: A Unified RAG Framework for Knowledge-Intensive Queries with Decomposition, Break-Down Reasoning, and Iterative Rewriting.* Findings of ACL: EMNLP 2025, pp. 18795–18810. DOI 10.18653/v1/2025.findings-emnlp.1022.

**What the paper reports.**
- Pipeline: a named-entity model extracts entities; the LLM writes several entity-grounded single-hop sub-queries (8 in their best multi-hop setting); results for the original query and all sub-queries are pooled, reranked and threshold-filtered; the LLM checks each sub-fact separately ("break-down" reasoning) before composing an answer.
- The answer is scored with RAGAs; below a threshold (or when the LLM signals low confidence) the system rewrites the query from its previous reasoning, avoiding earlier sub-queries, and loops. Iteration caps of 1–5 were tested with diminishing returns after the first few.
- Evaluated on HotPotQA, 2WikiMultihopQA, StrategyQA, MedMCQA, MedQA, FEVER, SciFact and others (first 500 questions per dataset) with LLaMA-3.1-8B, GPT-3.5-Turbo, Gemini-1.5-Flash and GPT-4o, against CoT, ReAct, Self-Ask, ITER-RETGEN and BlendFilter. Example: with GPT-3.5-Turbo, HotPotQA EM 0.584 vs. 0.498 for the best baseline; with GPT-4o, ITER-RETGEN was ahead on HotPotQA EM.

**Limitations.** Authors: the RAGAs-based acceptance threshold may introduce bias. *Our reading:* no per-query LLM-call count or latency is reported, and a configuration with ~8 sub-queries plus verification plus iterations implies many LLM calls per question; results use 500-question subsets.

**Relevance to Contexta (hypotheses).** The "retrieve per sub-query, pool, rerank" pattern fits comparison questions and is cheap to prototype on top of `ResearchRetriever`. RAGAs-gated iteration is too expensive for a free-tier deployment as a default; a cheaper gate (retrieval-score or coverage heuristics) should be tried first.

### 2.2 Q-PRM (Findings of EMNLP 2025)

**Citation.** Xiaopeng Ye, Chen Xu, Chaoliang Zhang, Zhaocheng Du, Jun Xu, Gang Wang, Zhenhua Dong. *Q-PRM: Adaptive Query Rewriting for Retrieval-Augmented Generation via Step-level Process Supervision.* Findings of ACL: EMNLP 2025, pp. 15113–15128. DOI 10.18653/v1/2025.findings-emnlp.817.

**What the paper reports.**
- Rewriting is modelled as a sequence of atomic actions — decomposition, disambiguation, abstraction, expansion — so the right *amount* of rewriting can differ per query (avoiding over-/under-rewriting).
- MCTS over action sequences produces step-level rewards (answer-score gain over the raw query, averaged over rollouts); the rewriter is fine-tuned on high-reward trajectories, then improved by reinforced self-training; a small process reward model (Qwen2.5-0.5B) is trained pairwise and guides beam-search decoding at inference.
- Rewriters LLaMA3-8B-Instruct / Qwen2.5-7B-Instruct, E5-large-v2 over Wikipedia (FlashRAG), reader Qwen-max; HotpotQA, StrategyQA, AmbigQA, PopQA plus generalisation sets; baselines include LLM rewrite, HyDE, Query2Doc, SFT, RaFe, RRR. Reported as best or second-best across query types; an online A/B test on a commercial Chinese RAG system is also reported.

**Limitations.** Authors: pre-retrieval rewriting only (not iterative RAG); PRM-guided multi-candidate decoding adds inference latency; rule-based rewards / GRPO not explored.

**Relevance to Contexta (hypotheses).** Full Q-PRM (MCTS data generation, RL training, PRM decoding) is out of scope for a single-developer project. The transferable idea is the **action vocabulary**: classifying a question into {keep, expand, disambiguate, decompose} before choosing a rewrite strategy is a cheap, testable heuristic version of "adaptive" rewriting.

### 2.3 Reinforced Query Reasoners (EMNLP 2025, main)

**Citation.** Xubo Qin, Jun Bai, Jiaqi Li, Zixia Jia, Zilong Zheng. *Reinforced Query Reasoners for Reasoning-intensive Retrieval Tasks.* Proc. EMNLP 2025, pp. 21250–21263. DOI 10.18653/v1/2025.emnlp-main.1078.

**What the paper reports.**
- Small models (Qwen2.5-7B/1.5B-Instruct) are trained with GRPO to rewrite a query into a "reasoned query" (a pseudo-answer with reasoning). The reward is how much a frozen embedder (bge-base-en-v1.5) increases similarity between the reasoned query and known-relevant documents versus the original query — no retrieval run or reward model needed.
- Training data from Stack Exchange preference data (~30k questions in the better variant).
- On BRIGHT (nDCG@10) with BM25: original queries 14.5; RQR-7B 27.9 (reported above GPT-4o at 26.5 and close to DeepSeek-R1 at 29.6); RQR-1.5B 24.6; combined with the ReasonIR retriever, 31.9.

**Limitations.** Authors: evaluated only on reasoning-intensive retrieval; training answers not carefully cleaned; Qwen2.5 base. The paper says code and data will be released — *we did not verify a release*. Cost comparisons are approximations based on base-model prices.

**Relevance to Contexta (hypotheses).** Strongest evidence that a **small local model** can do useful rewriting, which matters for the local-vs-hosted comparison. BRIGHT is not our domain (ML/NLP paper search), so gains must be re-measured on our own set. The embedding-similarity reward is a cheap *offline evaluation* signal we can reuse without RL: "does the rewrite move the query closer to the gold paper?"

### 2.4 Masking or Mitigating? (Findings of ACL 2026)

**Citation.** Agam Goyal, Koyel Mukherjee, Apoorv Saxena, Anirudh Phukan, Eshwar Chandrasekharan, Hari Sundaram. *Masking or Mitigating? Deconstructing the Impact of Query Rewriting on Retriever Biases in RAG.* Findings of ACL: ACL 2026, pp. 8517–8530. Anthology ID 2026.findings-acl.414 (arXiv 2604.06097). No DOI was shown on the page we checked.

**What the paper reports.**
- Five query-enhancement methods (LLM rewrite, HyDE, Query2Doc, and continually-pretrained variants of the latter two) × six dense/late-interaction retrievers, measured on four retriever biases — brevity, literal match, answer position, repetition — using paired biased/control documents (ColDeR benchmark) and paired t-tests.
- Simple LLM rewriting gives the largest aggregate bias reduction (mean |t| 8.72 → 4.02, ≈54%), but barely helps when several biases are combined adversarially; pseudo-document methods help less on aggregate but more in the combined case for some retrievers.
- Mechanistic analysis: rewriting's gains partly come from increased score variance rather than decorrelation from bias features — i.e. it can *mask* bias. Effects are retriever-dependent; brevity bias is largely a document-encoding problem that query-side methods cannot fix.

**Limitations.** Authors list benchmark/retriever scope, coverage of techniques, and no downstream (answer-quality) evaluation.

**Relevance to Contexta (hypotheses).** This is the methodological warning for Phase 3: measure rewriting **per retriever and per failure type**, include adversarial/combined cases, and keep the original query in the loop (Query2Doc-style concatenation, or fusion with the unrewritten results) rather than replacing it.

## 3. Proposed staged experiment

### 3.1 Pre-requisites (Phase 2)
- **Evaluation set** — ~150 ML/NLP questions with gold paper IDs: single-paper factual, paraphrased (vocabulary mismatch), comparison (2+ papers), and unanswerable-from-corpus. Built from the library + a frozen arXiv snapshot so runs are reproducible (live arXiv results drift).
- **Metrics** — retrieval: Recall@k, MRR, nDCG@10 against gold IDs; answer: citation precision (cited source is a gold source), citation-marker validity (already in `citation_check`), abstention correctness on unanswerable questions; cost: LLM calls, tokens, p50/p95 latency.
- **Baseline** — Phase 1 retrieval unchanged (condition 1 below), measured first. Phase 2 retrieval improvements (hybrid, reranking) must be frozen before Phase 3 comparisons, or the effects will be confounded.

### 3.2 Conditions

| # | Condition | Implementation sketch in Contexta | Primary question |
|---|---|---|---|
| 1 | Original query, no rewriting | Phase 1 `ResearchRetriever` | Baseline |
| 2 | Single LLM rewrite | New `rewrite` node before `retrieve`; retrieve with the rewrite only | Does rewriting alone help, and on which question types? |
| 3 | Multiple variants + fusion | 3 rewrites + original; reciprocal-rank fusion of the 4 result lists | Is keeping the original query (per §2.4) better than replacing it? |
| 4 | Decomposition | Sub-queries for comparison/multi-part questions; per-sub-query retrieval, pooled and re-ranked (UniRAG-style, without RAGAs loop) | Does coverage of multi-paper questions improve? |
| 5 | Selective iterative rewriting | Rewrite only when a retrieval-gap signal fires (top score < τ, or a decomposed sub-question has no source); max 2 iterations — a bounded conditional edge in the existing graph | Can we get most of 2–4's gain at a fraction of the cost? |
| 6 | Local vs hosted rewriter | Run 2–5 with a hosted model and with a local model via Ollama | Quality/latency/cost trade-off |

### 3.3 Hypotheses (ours, to be tested)
- H1: Condition 2 improves Recall@5 on paraphrased questions but can hurt single-paper factual questions (over-rewriting).
- H2: Condition 3 dominates condition 2 because the original query's results are preserved.
- H3: Condition 4 is the only one that materially improves comparison questions.
- H4: Condition 5 recovers ≥ 70% of the best condition's Recall@5 gain with ≤ 40% of its extra LLM calls.
- H5: A 7–8B local model is within a few points of a hosted model for rewriting (rewrites are short and structured), but slower on CPU-only hardware.

### 3.4 Guarding against the ACL 2026 failure mode
Report results per question type and per retriever (MiniLM dense, keyword-only, hybrid); include a small adversarial subset (short "keyword-stuffed" decoy abstracts) and check whether rewriting reduces or merely masks the decoys' advantage.

## 4. Ollama as an optional local backend

**What exists today in Contexta.** The `custom` provider already speaks the OpenAI protocol, so Ollama works without code changes: `LLM_PROVIDERS=custom LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=<model>`. Ollama's documentation describes JSON-schema-constrained structured outputs via its `format` parameter, also usable through its OpenAI-compatible endpoint (verified in Ollama's blog post; not yet tested with Contexta).

**Evaluation protocol before choosing a model** (no model is chosen yet):

| Criterion | How to measure |
|---|---|
| Rewrite quality | Recall@5 / nDCG@10 deltas on the §3.1 set (conditions 2–5) |
| Structured output reliability | % of rewrite calls returning schema-valid JSON over 500 calls, with and without constrained decoding |
| Latency | p50/p95 per rewrite on the target machine (CPU-only and GPU if available) |
| Memory | Peak RSS / VRAM during a 20-request burst |
| Hardware compatibility | Runs on the owner's laptop and on the deployment target; record OS/CPU/GPU |

Candidate families to include (all to be measured, none endorsed): a ~1.5B and a ~7B instruction model from the Qwen2.5 line (the base used by RQR, §2.3), an 8B Llama 3.x instruct model, and one current small model available in Ollama's library at the time of the experiment. Note the deployment constraint: the free 512 MB tier cannot host a local LLM, so local inference is a development/self-hosting option, not the hosted demo's default.

## 5. Risks
- Rewriting adds 1–8 LLM calls per question; on free tiers this multiplies rate-limit failures. Mitigation: selective rewriting (condition 5), caching rewrites, a hard per-request call budget in graph state.
- Rewrites can introduce hallucinated terms that retrieve plausible but wrong papers. Mitigation: keep the original query in fusion; show the rewrite in the `workflow` trace.
- Evaluation-set leakage: if the same LLM writes questions and rewrites, results inflate. Mitigation: questions written or reviewed by a human.

## Sources
- [UniRAG — ACL Anthology](https://aclanthology.org/2025.findings-emnlp.1022/) · [PDF](https://aclanthology.org/2025.findings-emnlp.1022.pdf)
- [Q-PRM — ACL Anthology](https://preview.aclanthology.org/dashboard/2025.findings-emnlp.817) · [PDF](https://aclanthology.org/2025.findings-emnlp.817.pdf)
- [Reinforced Query Reasoners — ACL Anthology](https://preview.aclanthology.org/setup/2025.emnlp-main.1078) · [PDF](https://aclanthology.org/2025.emnlp-main.1078.pdf) · [EMNLP 2025 talk](https://underline.io/lecture/130830-reinforced-query-reasoners-for-reasoning-intensive-retrieval-tasks)
- [Masking or Mitigating? — ACL Anthology](https://preview.aclanthology.org/ingest-acl/2026.findings-acl.414/) · [arXiv 2604.06097](https://arxiv.org/abs/2604.06097) · [HTML](https://arxiv.org/html/2604.06097v1)
- [Ollama — Structured outputs](https://registry.ollama.ai/blog/structured-outputs)
