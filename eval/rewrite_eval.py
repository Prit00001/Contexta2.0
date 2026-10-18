"""Phase 3 query-rewriting comparison (research brief section 3.2, conditions 1-5; 6 via REWRITE_PROVIDERS).

    PYTHONPATH=. python eval/run_eval.py --rewrite all                        # every condition
    PYTHONPATH=. python eval/run_eval.py --rewrite multi --limit 20
    PYTHONPATH=. python eval/run_eval.py --rewrite off,multi --retrievers all --decoys

Rewriting needs a real LLM call per question per condition (one call; ``selective`` only when it
fires), through REWRITE_PROVIDERS if set, else the main provider chain - so this is opt-in like
``--with-llm``. ``--rewrite off`` needs no LLM. Retrieval is local-library only, scored on the full
ranking (min_score=0), exactly like the baseline retrieval metrics, so ``off`` + ``hybrid``
reproduces them. Each question is rewritten once per condition and the same rewrites are scored on
every retriever, so retrievers are compared on identical queries.

Per condition, retriever and question type: Recall@1/@5, MRR, nDCG@10, plus

* ``llm_calls`` and rewrite latency (p50/p95) - the cost side of H4;
* ``abstain_unanswerable``: share of unanswerable questions where nothing clears the configured
  MIN_SCORE (retrieval-level abstention; ``hybrid`` only, the scale MIN_SCORE is defined on);
* ``gold_sim_delta``: mean change in cosine similarity between the best query and the gold
  document vs. the original question (Reinforced Query Reasoners' signal, brief section 2.3, as a
  diagnostic only);
* with ``--decoys`` (brief section 3.4): ``decoy_win`` - share of answerable questions where a decoy
  outranks every gold document - overall and per decoy kind.

Retrievers (``--retrievers``, default ``hybrid``): ``hybrid`` (what /ask uses: 0.8 cosine + 0.2
keyword overlap), ``hybrid_bm25`` (plus RRF-fused BM25, ADR-003), ``dense`` (cosine only),
``keyword`` (BM25 only), ``hybrid_rerank`` (hybrid + cross-encoder reranking, P2-07; downloads the model).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from run_eval import _mean, ndcg_at_k, recall_at_k, reciprocal_rank

from app.config import get_settings
from core.embeddings import Embedder
from core.generator import Generator
from core.lc_retriever import ResearchRetriever
from core.query_rewrite import MODES, fuse, prompt_mode, retrieval_queries, rewrite_question
from core.reranker import Reranker
from core.retriever import Retriever
from core.sources import normalize_source
from core.text import keywords
from data.documents import DOCUMENTS

DEPTH = 10
RETRIEVERS = ("hybrid", "hybrid_bm25", "dense", "keyword", "hybrid_rerank")
DECOY_KINDS = ("literal", "repetition")


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[min(int(len(values) * p), len(values) - 1)], 1)


# ---- decoys (brief section 3.4) --------------------------------------------------------------


def make_decoys(questions: list[dict]) -> list[dict]:
    """Two keyword-stuffed abstracts per answerable question, carrying none of the answer:

    * ``literal`` - short, the question's keywords once (brevity + literal-match bias);
    * ``repetition`` - the same keywords repeated (repetition bias).

    The biases come from the ColDeR setup used by "Masking or Mitigating?" (brief section 2.4);
    the documents are generated, not taken from that benchmark. Deterministic, no LLM.
    """
    decoys = []
    for q in questions:
        if q["type"] == "unanswerable":
            continue
        terms = keywords(q["question"], 6)
        if not terms:
            continue
        phrase = " ".join(terms)
        decoys.append(_decoy(q["id"], "literal", phrase.title(), f"This work studies {phrase}."))
        decoys.append(_decoy(q["id"], "repetition", f"On {phrase}", " ".join([f"{phrase}."] * 4)))
    return decoys


def _decoy(qid: str, kind: str, title: str, content: str) -> dict:
    return {"id": f"decoy_{qid}_{kind}", "source": "local", "title": title, "url": "", "content": content}


def decoy_kind(document_id: str) -> str | None:
    return document_id.rsplit("_", 1)[-1] if document_id.startswith("decoy_") else None


# ---- harness ------------------------------------------------------------------------------------


class _Rankers:
    def __init__(self, embedder, library, settings):
        self.embedder, self.library = embedder, library

        def research(bm25: bool, reranker=None) -> ResearchRetriever:
            return ResearchRetriever(
                embedder=embedder, library=library, arxiv=None, arxiv_enabled=False,
                arxiv_budget_s=settings.arxiv_budget_s, min_score=0.0, hybrid_bm25_enabled=bm25,
                reranker=reranker, rerank_candidates=settings.rerank_candidates,
            )  # fmt: skip

        self.hybrid, self.hybrid_bm25 = research(False), research(True)
        self._research = research
        self._hybrid_rerank = None

    @property
    def hybrid_rerank(self) -> ResearchRetriever:
        """Built on first use: loads the cross-encoder (RERANK_MODEL or the small default)."""
        if self._hybrid_rerank is None:
            from run_eval import DEFAULT_RERANK_MODEL

            settings = get_settings()
            reranker = Reranker(settings.rerank_model or DEFAULT_RERANK_MODEL, settings.model_cache_dir)
            if not reranker.available:
                raise SystemExit(f"hybrid_rerank: could not load {reranker.model_name} ({reranker.error})")
            self._hybrid_rerank = self._research(False, reranker)
        return self._hybrid_rerank

    async def rank(self, kind: str, query: str) -> list[dict]:
        if kind in ("hybrid", "hybrid_bm25", "hybrid_rerank"):
            return (await getattr(self, kind).aretrieve(query, DEPTH, use_arxiv=False)).sources
        if kind == "dense":
            hits = self.library.search((await self.embedder.embed([query]))[0], DEPTH)
        else:
            hits = self.library.bm25_search(query, DEPTH)
        return [normalize_source({**doc, "score": score}) for doc, score in hits]


async def run_rewrite_comparison(
    questions: list[dict],
    modes: list[str],
    retrievers: list[str] | None = None,
    decoys: bool = False,
    pace: float = 0.0,
    cache_path: Path | None = None,
) -> dict:
    retrievers = retrievers or ["hybrid"]
    settings = get_settings()
    specs = settings.rewrite_providers or settings.providers
    if any(m != "off" for m in modes) and not specs:
        raise SystemExit(
            "--rewrite needs an LLM: set GROQ_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY, or "
            "LLM_BASE_URL + LLM_MODEL (e.g. a local Ollama), optionally REWRITE_PROVIDERS=custom."
        )
    embedder = Embedder(settings.embed_backend, settings.embed_model, settings.model_cache_dir)
    corpus = DOCUMENTS + (make_decoys(questions) if decoys else [])
    library = Retriever(embedder, settings.cache_dir / ("embeddings_decoys.npz" if decoys else "embeddings.npz"))
    library.build(corpus)
    rankers = _Rankers(embedder, library, settings)
    min_score = settings.min_score_for(embedder.backend)
    doc_text = {d["id"]: f"{d['title']}. {d['content']}" for d in DOCUMENTS}  # as core.retriever indexes them
    doc_vecs = dict(zip(doc_text, await embedder.embed(list(doc_text.values())), strict=True))

    report: dict = {
        "embedding_backend": embedder.backend,
        "rewriter": [f"{s.name}:{s.model}" for s in specs],
        "min_score": min_score,
        "gap_score": settings.rewrite_gap_score,
        "decoys": len(corpus) - len(DOCUMENTS),
        "conditions": {},
    }
    cache = json.loads(cache_path.read_text()) if cache_path is not None and cache_path.exists() else None
    if cache_path is not None and cache is None:
        cache = {}
    async with httpx.AsyncClient() as client:
        rewriter = Generator(specs, client, settings.llm_timeout)
        for mode in modes:
            plans = []
            for q in questions:
                plan = await _plan(q, mode, rankers, rewriter, settings, cache)
                if pace and plan["fresh_call"]:
                    await asyncio.sleep(pace)  # stay under free-tier rate limits (--pace)
                plans.append(plan)
            if cache_path is not None:
                cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
            for kind in retrievers:
                rows = [
                    await _score(q, plan, kind, rankers, embedder, doc_vecs, min_score)
                    for q, plan in zip(questions, plans, strict=True)
                ]
                report["conditions"][f"{mode}/{kind}"] = {"summary": _summarize(rows, decoys), "detail": rows}
    return report


async def _plan(q: dict, mode: str, rankers: _Rankers, rewriter, settings, cache: dict | None = None) -> dict:
    """Decide the queries for one question under one condition - once, shared by every retriever.
    ``selective`` judges weakness on the hybrid ranking, as /ask does.

    ``cache`` (``--rewrite-cache``) maps "<prompt mode>|<question>" to a previous successful rewrite, so
    reruns and ``selective`` (same prompt as ``multi``) don't spend provider quota again. A cached
    rewrite still counts as one LLM call with its original latency: the report describes what the
    condition costs in production, not what this run spent. ``plan["fresh_call"]`` says whether a
    real call was made now (used for pacing)."""
    question = q["question"]
    plan = {"rewrites": None, "llm_calls": 0, "rewrite_ms": 0.0, "diagnostics": [], "fresh_call": False}

    async def rewrite() -> None:
        key = f"{prompt_mode(mode)}|{question}"
        if cache is not None and key in cache:
            plan["rewrites"], plan["rewrite_ms"] = cache[key]["rewrites"], cache[key]["ms"]
        else:
            t0 = time.perf_counter()
            plan["rewrites"], plan["diagnostics"] = await rewrite_question(question, mode, rewriter.complete)
            plan["rewrite_ms"] = (time.perf_counter() - t0) * 1000
            plan["fresh_call"] = True
            if cache is not None and plan["rewrites"] is not None:  # never cache a failure
                cache[key] = {"rewrites": plan["rewrites"], "ms": round(plan["rewrite_ms"], 1)}
        plan["llm_calls"] = 1

    if mode in ("single", "multi", "decompose"):
        await rewrite()
    elif mode == "selective":
        first = await rankers.rank("hybrid", question)
        if not first or max(s["score"] for s in first) < settings.rewrite_gap_score:
            await rewrite()
    plan["queries"] = retrieval_queries(question, plan["rewrites"], mode)
    return plan


async def _score(q: dict, plan: dict, kind: str, rankers: _Rankers, embedder, doc_vecs, min_score) -> dict:
    question, queries = q["question"], plan["queries"]
    lists = [await rankers.rank(kind, x) for x in queries]
    sources = lists[0] if len(lists) == 1 else fuse(lists, DEPTH)
    ranked = [s["document_id"] for s in sources]
    row = {
        "id": q["id"],
        "type": q["type"],
        "queries": queries,
        "llm_calls": plan["llm_calls"],
        "rewrite_ms": round(plan["rewrite_ms"], 1),
        "rewrite_failed": plan["llm_calls"] == 1 and plan["rewrites"] is None,
        "diagnostics": plan["diagnostics"],
        "ranked": ranked,
    }
    if q["type"] == "unanswerable":
        row["abstained"] = not any(s["score"] >= min_score for s in sources) if kind == "hybrid" else None
        return row
    gold = set(q["gold_document_ids"])
    qvecs = await embedder.embed([question] + [x for x in queries if x != question])
    original, rewritten = qvecs[0], (qvecs[1:] if len(qvecs) > 1 else qvecs[:1])
    sims = [max(float(v @ doc_vecs[g]) for v in rewritten) - float(original @ doc_vecs[g]) for g in gold]
    gold_pos = min((i for i, d in enumerate(ranked) if d in gold), default=len(ranked))
    winning = [decoy_kind(d) for d in ranked[:gold_pos] if decoy_kind(d)]
    row.update(
        {
            "recall@1": recall_at_k(ranked, gold, 1),
            "recall@5": recall_at_k(ranked, gold, 5),
            "mrr": reciprocal_rank(ranked, gold),
            "ndcg@10": ndcg_at_k(ranked, gold, 10),
            "gold_sim_delta": round(sum(sims) / len(sims), 4),
            "decoy_win": bool(winning),
            "decoy_win_kinds": sorted(set(winning)),
        }
    )
    return row


def _summarize(rows: list[dict], decoys: bool = False) -> dict:
    out: dict = {}
    answerable = [r for r in rows if r["type"] != "unanswerable"]
    for t in sorted({r["type"] for r in answerable}) + ["overall"]:
        group = answerable if t == "overall" else [r for r in answerable if r["type"] == t]
        out[t] = {"n": len(group)} | {
            m: _mean([r[m] for r in group]) for m in ("recall@1", "recall@5", "mrr", "ndcg@10")
        }
        out[t]["gold_sim_delta"] = _mean([r["gold_sim_delta"] for r in group])
        if decoys:
            out[t]["decoy_win"] = _mean([float(r["decoy_win"]) for r in group])
    unanswerable = [r for r in rows if r["type"] == "unanswerable" and r["abstained"] is not None]
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
    if decoys:
        out["decoys"] = {
            k: _mean([float(k in r["decoy_win_kinds"]) for r in answerable]) for k in DECOY_KINDS
        }  # share of answerable questions where this decoy kind outranks all gold
    return out


def markdown_table(report: dict) -> str:
    decoys = bool(report.get("decoys"))
    head = "| Condition | Retriever | Type | n | R@1 | R@5 | MRR | nDCG@10 | Δsim(gold) |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    lines = [
        f"Rewriter: {', '.join(report['rewriter']) or 'none'} · embeddings: {report['embedding_backend']} · "
        f"MIN_SCORE {report['min_score']} · gap {report['gap_score']} · decoys {report.get('decoys', 0)}",
        "",
        head + (" Decoy win |" if decoys else ""),
        sep + ("---|" if decoys else ""),
    ]
    for key, data in report["conditions"].items():
        mode, kind = key.split("/")
        for t, s in data["summary"].items():
            if t in ("cost", "decoys"):
                continue
            cells = [mode, kind, t, s["n"], s["recall@1"], s["recall@5"], s["mrr"], s["ndcg@10"], s["gold_sim_delta"]]
            if decoys:
                cells.append(s["decoy_win"])
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += [
        "",
        "| Condition | Retriever | LLM calls/q | Rewrite failures | Rewrite p50/p95 ms | Abstain (unanswerable) |"
        + (" Decoy win: literal / repetition |" if decoys else ""),
        "|---|---|---|---|---|---|" + ("---|" if decoys else ""),
    ]
    for key, data in report["conditions"].items():
        mode, kind = key.split("/")
        c = data["summary"]["cost"]
        row = (
            f"| {mode} | {kind} | {c['llm_calls_per_question']} | {c['rewrite_failures']} | "
            f"{c['rewrite_ms_p50']} / {c['rewrite_ms_p95']} | {c['abstain_unanswerable']} |"
        )
        if decoys:
            d = data["summary"]["decoys"]
            row += f" {d['literal']} / {d['repetition']} |"
        lines.append(row)
    return "\n".join(lines)


def _parse_list(value: str, allowed: tuple[str, ...], flag: str) -> list[str]:
    items = list(allowed) if value == "all" else [m.strip() for m in value.split(",") if m.strip()]
    unknown = [m for m in items if m not in allowed]
    if unknown:
        raise SystemExit(f"{flag}: unknown value(s) {unknown}; choose from {list(allowed)} or 'all'")
    return items


def parse_modes(value: str) -> list[str]:
    return _parse_list(value, MODES, "--rewrite")


def parse_retrievers(value: str) -> list[str]:
    return _parse_list(value, RETRIEVERS, "--retrievers")
