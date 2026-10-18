"""Phase 3 query-rewriting comparison (research brief section 3.2, conditions 1-5; 6 via REWRITE_PROVIDERS).

    PYTHONPATH=. python eval/run_eval.py --rewrite all          # every condition, one table
    PYTHONPATH=. python eval/run_eval.py --rewrite multi --limit 20

Rewriting needs a real LLM call per question per condition (one call; ``selective`` only when it
fires), through REWRITE_PROVIDERS if set, else the main provider chain - so this is opt-in like
``--with-llm``. Retrieval is local-library only, scored on the full ranking (min_score=0), exactly
like the baseline retrieval metrics, so condition ``off`` reproduces them.

Per condition and question type it reports Recall@1/@5, MRR and nDCG@10, plus:

* ``llm_calls`` and rewrite latency (p50/p95) - the cost side of H4;
* ``abstain_unanswerable``: share of unanswerable questions where nothing clears the configured
  MIN_SCORE (retrieval-level abstention) - rewriting must not turn "no evidence" into
  plausible-but-wrong evidence (brief section 5);
* ``gold_sim_delta``: mean change in cosine similarity between the best query and the gold
  document vs. the original question - the embedding-similarity signal from Reinforced Query
  Reasoners (brief section 2.3), used here as an offline diagnostic, not a reward.
"""

from __future__ import annotations

import time

import httpx
from run_eval import _mean, ndcg_at_k, recall_at_k, reciprocal_rank

from app.config import get_settings
from core.embeddings import Embedder
from core.generator import Generator
from core.lc_retriever import ResearchRetriever
from core.query_rewrite import MODES, fuse, retrieval_queries, rewrite_question
from core.retriever import Retriever
from data.documents import DOCUMENTS

DEPTH = 10


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[min(int(len(values) * p), len(values) - 1)], 1)


async def run_rewrite_comparison(questions: list[dict], modes: list[str]) -> dict:
    settings = get_settings()
    specs = settings.rewrite_providers or settings.providers
    if any(m != "off" for m in modes) and not specs:
        raise SystemExit(
            "--rewrite needs an LLM: set GROQ_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY, or "
            "LLM_BASE_URL + LLM_MODEL (e.g. a local Ollama), optionally REWRITE_PROVIDERS=custom."
        )
    embedder = Embedder(settings.embed_backend, settings.embed_model, settings.model_cache_dir)
    library = Retriever(embedder, settings.cache_dir / "embeddings.npz")
    library.build(DOCUMENTS)
    retriever = ResearchRetriever(
        embedder=embedder, library=library, arxiv=None, arxiv_enabled=False,
        arxiv_budget_s=settings.arxiv_budget_s, min_score=0.0,
    )  # fmt: skip
    min_score = settings.min_score_for(embedder.backend)
    doc_text = {d["id"]: f"{d['title']}. {d['content']}" for d in DOCUMENTS}  # as core.retriever indexes them
    doc_vecs = dict(zip(doc_text, await embedder.embed(list(doc_text.values())), strict=True))

    report: dict = {
        "embedding_backend": embedder.backend,
        "rewriter": [f"{s.name}:{s.model}" for s in specs],
        "min_score": min_score,
        "gap_score": settings.rewrite_gap_score,
        "conditions": {},
    }
    async with httpx.AsyncClient() as client:
        rewriter = Generator(specs, client, settings.llm_timeout)
        for mode in modes:
            rows = []
            for q in questions:
                rows.append(await _one(q, mode, retriever, rewriter, embedder, doc_vecs, settings, min_score))
            report["conditions"][mode] = {"summary": _summarize(rows), "detail": rows}
    return report


async def _one(q, mode, retriever, rewriter, embedder, doc_vecs, settings, min_score) -> dict:
    question = q["question"]
    rewrites, calls, rewrite_ms, diagnostics = None, 0, 0.0, []

    async def rewrite() -> None:
        nonlocal rewrites, calls, rewrite_ms, diagnostics
        t0 = time.perf_counter()
        rewrites, diagnostics = await rewrite_question(question, mode, rewriter.complete)
        rewrite_ms = (time.perf_counter() - t0) * 1000
        calls = 1

    first = None
    if mode in ("single", "multi", "decompose"):
        await rewrite()
    elif mode == "selective":
        first = await retriever.aretrieve(question, DEPTH, use_arxiv=False)
        if not first.sources or max(s["score"] for s in first.sources) < settings.rewrite_gap_score:
            await rewrite()

    queries = retrieval_queries(question, rewrites, mode)
    if queries == [question] and first is not None:
        sources = first.sources
    else:
        results = [await retriever.aretrieve(x, DEPTH, use_arxiv=False) for x in queries]
        sources = results[0].sources if len(results) == 1 else fuse([r.sources for r in results], DEPTH)
    ranked = [s["document_id"] for s in sources]

    row = {
        "id": q["id"],
        "type": q["type"],
        "queries": queries,
        "llm_calls": calls,
        "rewrite_ms": round(rewrite_ms, 1),
        "rewrite_failed": calls == 1 and rewrites is None,
        "diagnostics": diagnostics,
    }
    if q["type"] == "unanswerable":
        row["abstained"] = not any(s["score"] >= min_score for s in sources)
        return row
    gold = set(q["gold_document_ids"])
    qvecs = await embedder.embed([question] + [x for x in queries if x != question])
    original, rewritten = qvecs[0], (qvecs[1:] if len(qvecs) > 1 else qvecs[:1])
    sims = [max(float(v @ doc_vecs[g]) for v in rewritten) - float(original @ doc_vecs[g]) for g in gold]
    row.update(
        {
            "ranked": ranked,
            "recall@1": recall_at_k(ranked, gold, 1),
            "recall@5": recall_at_k(ranked, gold, 5),
            "mrr": reciprocal_rank(ranked, gold),
            "ndcg@10": ndcg_at_k(ranked, gold, 10),
            "gold_sim_delta": round(sum(sims) / len(sims), 4),
        }
    )
    return row


def _summarize(rows: list[dict]) -> dict:
    out: dict = {}
    answerable = [r for r in rows if r["type"] != "unanswerable"]
    for t in sorted({r["type"] for r in answerable}) + ["overall"]:
        group = answerable if t == "overall" else [r for r in answerable if r["type"] == t]
        out[t] = {"n": len(group)} | {
            m: _mean([r[m] for r in group]) for m in ("recall@1", "recall@5", "mrr", "ndcg@10")
        }
        out[t]["gold_sim_delta"] = _mean([r["gold_sim_delta"] for r in group])
    unanswerable = [r for r in rows if r["type"] == "unanswerable"]
    rewrite_ms = [r["rewrite_ms"] for r in rows if r["llm_calls"]]
    out["cost"] = {
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "llm_calls_per_question": round(sum(r["llm_calls"] for r in rows) / len(rows), 3) if rows else 0.0,
        "rewrite_failures": sum(1 for r in rows if r["rewrite_failed"]),
        "rewrite_ms_p50": _pct(rewrite_ms, 0.5),
        "rewrite_ms_p95": _pct(rewrite_ms, 0.95),
        "abstain_unanswerable": round(sum(r["abstained"] for r in unanswerable) / len(unanswerable), 4)
        if unanswerable
        else None,
    }
    return out


def markdown_table(report: dict) -> str:
    lines = [
        f"Rewriter: {', '.join(report['rewriter']) or 'none'} · embeddings: {report['embedding_backend']} · "
        f"MIN_SCORE {report['min_score']} · gap {report['gap_score']}",
        "",
        "| Condition | Type | n | R@1 | R@5 | MRR | nDCG@10 | Δsim(gold) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for mode, data in report["conditions"].items():
        for t, s in data["summary"].items():
            if t == "cost":
                continue
            lines.append(
                f"| {mode} | {t} | {s['n']} | {s['recall@1']} | {s['recall@5']} | {s['mrr']} | {s['ndcg@10']} "
                f"| {s['gold_sim_delta']} |"
            )
    lines += [
        "",
        "| Condition | LLM calls/q | Rewrite failures | Rewrite p50/p95 ms | Abstain (unanswerable) |",
        "|---|---|---|---|---|",
    ]
    for mode, data in report["conditions"].items():
        c = data["summary"]["cost"]
        lines.append(
            f"| {mode} | {c['llm_calls_per_question']} | {c['rewrite_failures']} | "
            f"{c['rewrite_ms_p50']} / {c['rewrite_ms_p95']} | {c['abstain_unanswerable']} |"
        )
    return "\n".join(lines)


def parse_modes(value: str) -> list[str]:
    modes = list(MODES) if value == "all" else [m.strip() for m in value.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        raise SystemExit(f"--rewrite: unknown mode(s) {unknown}; choose from {list(MODES)} or 'all'")
    return modes
