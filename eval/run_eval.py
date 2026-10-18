"""Phase 2 evaluation harness (backlog P2-01).

Retrieval metrics (Recall@1/3/5, MRR, nDCG@10 per question type) need no LLM and no network -
they run by default, using whatever EMBED_BACKEND is configured (fastembed/real by default; set
EMBED_BACKEND=hash for a fast, fully offline dev loop, but never report those numbers as the
baseline - see docs/evaluation/EVALUATION_REPORT.md).

Answer-quality metrics (citation precision, marker validity, abstention correctness, outcome
distribution, cost/latency) need a real LLM call per question and are opt-in:

    python eval/run_eval.py                              # retrieval only, offline
    python eval/run_eval.py --with-llm                    # + answer-quality + cost (real API calls)
    python eval/run_eval.py --with-llm --limit 10          # quick smoke run
    python eval/run_eval.py --sweep-min-score             # MIN_SCORE calibration curve, offline

Scoped to the local 25-paper library only (arxiv_snapshot.jsonl is not yet populated - see
eval/dataset/ARXIV_SNAPSHOT.md); every dataset question's gold_document_ids are local doc_0XX IDs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from typing import Optional

import httpx

from app.config import get_settings
from core.embeddings import Embedder
from core.generator import Generator
from core.lc_retriever import ResearchRetriever
from core.retriever import Retriever
from data.documents import DOCUMENTS
from services.arxiv_client import ArxivClient
from services.research_graph import build_research_graph, run_research

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset" / "questions.jsonl"


def load_questions(path: Path = DATASET, limit: Optional[int] = None) -> list[dict]:
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return items[:limit] if limit else items


# ---- retrieval metrics --------------------------------------------------------------------


def recall_at_k(ranked_ids: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    found = len(set(ranked_ids[:k]) & gold)
    return found / len(gold)


def reciprocal_rank(ranked_ids: list[str], gold: set[str]) -> float:
    for i, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked_ids: list[str], gold: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, d in enumerate(ranked_ids[:k], start=1) if d in gold)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / idcg if idcg > 0 else 0.0


async def run_retrieval(questions: list[dict], top_k: int = 10, hybrid_bm25: bool = False) -> list[dict]:
    settings = get_settings()
    embedder = Embedder(settings.embed_backend, settings.embed_model, settings.model_cache_dir)
    library = Retriever(embedder, settings.cache_dir / "embeddings.npz")
    library.build(DOCUMENTS)
    retriever = ResearchRetriever(
        embedder=embedder, library=library, arxiv=None, arxiv_enabled=False,
        arxiv_budget_s=settings.arxiv_budget_s, min_score=0.0,  # score the full ranking, not just >= MIN_SCORE
        hybrid_bm25_enabled=hybrid_bm25,
    )  # fmt: skip

    results = []
    for q in questions:
        if q["type"] == "unanswerable":
            continue  # no gold to rank against; scored separately under abstention (--with-llm)
        gold = set(q["gold_document_ids"])
        t0 = time.perf_counter()
        result = await retriever.aretrieve(q["question"], top_k, use_arxiv=False)
        latency_ms = (time.perf_counter() - t0) * 1000
        ranked_ids = [s["document_id"] for s in result.sources]
        results.append(
            {
                "id": q["id"],
                "type": q["type"],
                "gold": sorted(gold),
                "ranked": ranked_ids,
                "recall@1": recall_at_k(ranked_ids, gold, 1),
                "recall@3": recall_at_k(ranked_ids, gold, 3),
                "recall@5": recall_at_k(ranked_ids, gold, 5),
                "mrr": reciprocal_rank(ranked_ids, gold),
                "ndcg@10": ndcg_at_k(ranked_ids, gold, 10),
                "latency_ms": round(latency_ms, 1),
            }
        )
    return results


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def summarize_retrieval(results: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r)
    summary = {}
    for t, rows in by_type.items():
        summary[t] = {
            "n": len(rows),
            "recall@1": _mean([r["recall@1"] for r in rows]),
            "recall@3": _mean([r["recall@3"] for r in rows]),
            "recall@5": _mean([r["recall@5"] for r in rows]),
            "mrr": _mean([r["mrr"] for r in rows]),
            "ndcg@10": _mean([r["ndcg@10"] for r in rows]),
        }
    summary["overall"] = {
        "n": len(results),
        "recall@1": _mean([r["recall@1"] for r in results]),
        "recall@3": _mean([r["recall@3"] for r in results]),
        "recall@5": _mean([r["recall@5"] for r in results]),
        "mrr": _mean([r["mrr"] for r in results]),
        "ndcg@10": _mean([r["ndcg@10"] for r in results]),
    }
    return summary


# ---- MIN_SCORE calibration (P2-FR-01) -------------------------------------------------------

SWEEP_THRESHOLDS = [round(0.025 * i, 3) for i in range(0, 25)]  # 0.0 .. 0.6
SWEEP_K = 5  # /ask's default top_k
RECALL_TOLERANCE = 0.02  # PRD_PHASE_2 section 1: no metric may drop more than 0.02 absolute


async def run_score_sweep(questions: list[dict], hybrid_bm25: bool = False) -> dict:
    """Score every question once at min_score=0 (true ranking with scores), then apply each candidate
    threshold to the collected rankings - no re-retrieval per threshold (ADR-002 section 4).

    Abstention here is retrieval-level: when nothing passes MIN_SCORE the workflow answers
    ``insufficient_evidence`` without calling an LLM, so it is measurable offline. Abstention by the
    LLM itself (sources retrieved, model says they are insufficient) needs ``--with-llm``.
    """
    settings = get_settings()
    embedder = Embedder(settings.embed_backend, settings.embed_model, settings.model_cache_dir)
    library = Retriever(embedder, settings.cache_dir / "embeddings.npz")
    library.build(DOCUMENTS)
    retriever = ResearchRetriever(
        embedder=embedder, library=library, arxiv=None, arxiv_enabled=False,
        arxiv_budget_s=settings.arxiv_budget_s, min_score=0.0, hybrid_bm25_enabled=hybrid_bm25,
    )  # fmt: skip
    rows = []
    for q in questions:
        result = await retriever.aretrieve(q["question"], 10, use_arxiv=False)
        rows.append({"type": q["type"], "gold": set(q["gold_document_ids"]), "ranked": result.sources})

    answerable = [r for r in rows if r["type"] != "unanswerable"]
    unanswerable = [r for r in rows if r["type"] == "unanswerable"]
    curve = []
    for t in SWEEP_THRESHOLDS:
        recalls, precisions, false_abstain = [], [], 0
        for r in answerable:
            kept = [s["document_id"] for s in r["ranked"] if s["score"] >= t][:SWEEP_K]
            recalls.append(recall_at_k(kept, r["gold"], SWEEP_K))
            if kept:
                precisions.append(len(set(kept) & r["gold"]) / len(kept))
            else:
                false_abstain += 1
        abstained = sum(1 for r in unanswerable if not any(s["score"] >= t for s in r["ranked"]))
        curve.append(
            {
                "min_score": t,
                "recall@5": _mean(recalls),
                "precision@5": _mean(precisions),
                "false_abstention_rate": round(false_abstain / len(answerable), 4) if answerable else 0.0,
                "abstention_rate_unanswerable": round(abstained / len(unanswerable), 4) if unanswerable else 0.0,
            }
        )
    base = curve[0]["recall@5"]
    within = [c for c in curve if base - c["recall@5"] <= RECALL_TOLERANCE and c["false_abstention_rate"] == 0.0]
    best = max(within, key=lambda c: (c["abstention_rate_unanswerable"], -c["min_score"]))
    top1 = sorted(r["ranked"][0]["score"] for r in rows if r["ranked"] and r["type"] != "unanswerable")
    top1_un = sorted(r["ranked"][0]["score"] for r in unanswerable if r["ranked"])
    return {
        "embedding_backend": embedder.backend,
        "embedding_degraded": embedder.degraded,
        "hybrid_bm25": hybrid_bm25,
        "k": SWEEP_K,
        "curve": curve,
        "recommended_min_score": best["min_score"],
        "rule": (
            f"highest abstention on unanswerable among thresholds with recall@{SWEEP_K} within "
            f"{RECALL_TOLERANCE} of min_score=0 and no answerable question left without sources "
            "(ties -> lowest threshold)"
        ),
        "top1_score_answerable_min": round(top1[0], 4) if top1 else None,
        "top1_score_unanswerable_max": round(top1_un[-1], 4) if top1_un else None,
    }


# ---- answer-quality metrics (--with-llm) --------------------------------------------------


async def run_answers(questions: list[dict]) -> list[dict]:
    settings = get_settings()
    embedder = Embedder(settings.embed_backend, settings.embed_model, settings.model_cache_dir)
    library = Retriever(embedder, settings.cache_dir / "embeddings.npz")
    library.build(DOCUMENTS)
    async with httpx.AsyncClient() as client:
        arxiv = ArxivClient(client, settings.arxiv_max_results, settings.arxiv_cache_ttl)
        generator = Generator(settings.providers, client, settings.llm_timeout)
        retriever = ResearchRetriever(
            embedder=embedder, library=library, arxiv=arxiv, arxiv_enabled=False,
            arxiv_budget_s=settings.arxiv_budget_s, min_score=settings.min_score_for(embedder.backend),
        )  # fmt: skip
        graph = build_research_graph(generator, settings.llm_max_attempts)

        results = []
        for q in questions:
            t0 = time.perf_counter()
            state = await run_research(graph, retriever, q["question"], 5, options={"use_arxiv": False})
            total_ms = (time.perf_counter() - t0) * 1000
            gold = set(q.get("gold_document_ids", []))
            citation_report = state.get("citation_report")
            cited_doc_ids = {
                c.get("document_id") for c in getattr(citation_report, "citations", []) if c.get("document_id")
            }
            valid_markers = getattr(citation_report, "valid_markers", None)
            results.append(
                {
                    "id": q["id"],
                    "type": q["type"],
                    "outcome": state.get("outcome"),
                    "provider": state.get("provider"),
                    "model": state.get("model"),
                    "attempts": state.get("attempts"),
                    "total_ms": round(total_ms, 1),
                    "trace": state.get("trace", []),
                    "citation_precision": (
                        len(cited_doc_ids & gold) / len(cited_doc_ids) if cited_doc_ids and gold else None
                    ),
                    "valid_markers": valid_markers,
                    "abstained_correctly": (
                        state.get("outcome") == "insufficient_evidence" if q["type"] == "unanswerable" else None
                    ),
                }
            )
        return results


def summarize_answers(results: list[dict]) -> dict:
    outcomes: dict[str, int] = {}
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    unanswerable = [r for r in results if r["type"] == "unanswerable"]
    abstention_correct = [r for r in unanswerable if r["abstained_correctly"]]
    precisions = [r["citation_precision"] for r in results if r["citation_precision"] is not None]
    latencies = sorted(r["total_ms"] for r in results)

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return round(latencies[idx], 1)

    return {
        "outcome_distribution": outcomes,
        "citation_precision_mean": _mean(precisions),
        "abstention_correctness": (round(len(abstention_correct) / len(unanswerable), 4) if unanswerable else None),
        "n_unanswerable": len(unanswerable),
        "latency_p50_ms": pct(0.50),
        "latency_p95_ms": pct(0.95),
        "providers_used": sorted({r["provider"] for r in results if r.get("provider")}),
    }


# ---- report ---------------------------------------------------------------------------------


def write_markdown(report: dict, path: Path) -> None:
    lines = ["# Evaluation Report (generated)", "", f"Dataset: `{DATASET}`, n={report['n_questions']}", ""]
    lines += [
        "## Retrieval",
        "",
        "| Type | n | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@10 |",
        "|---|---|---|---|---|---|---|",
    ]
    for t, s in report["retrieval"].items():
        lines.append(
            f"| {t} | {s['n']} | {s['recall@1']} | {s['recall@3']} | {s['recall@5']} | {s['mrr']} | {s['ndcg@10']} |"
        )
    if report.get("answers"):
        a = report["answers"]
        lines += [
            "",
            "## Answers (--with-llm)",
            "",
            f"- Outcome distribution: {a['outcome_distribution']}",
            f"- Citation precision (mean): {a['citation_precision_mean']}",
            f"- Abstention correctness on unanswerable (n={a['n_unanswerable']}): {a['abstention_correctness']}",
            f"- Latency p50/p95: {a['latency_p50_ms']}ms / {a['latency_p95_ms']}ms",
            f"- Providers used: {', '.join(a['providers_used']) or 'none'}",
        ]
    path.write_text("\n".join(lines) + "\n")


async def main_async(args: argparse.Namespace) -> None:
    questions = load_questions(limit=args.limit)
    if args.sweep_min_score:
        sweep = await run_score_sweep(questions, hybrid_bm25=args.hybrid_bm25)
        out = ROOT / f"min_score_sweep_{sweep['embedding_backend']}.json"
        out.write_text(json.dumps(sweep, indent=2))
        print("| MIN_SCORE | Recall@5 | Precision@5 | False abstention | Abstention (unanswerable) |")
        print("|---|---|---|---|---|")
        for c in sweep["curve"]:
            print(
                f"| {c['min_score']} | {c['recall@5']} | {c['precision@5']} | {c['false_abstention_rate']} "
                f"| {c['abstention_rate_unanswerable']} |"
            )
        print(f"\nbackend={sweep['embedding_backend']} recommended MIN_SCORE={sweep['recommended_min_score']}")
        print(f"rule: {sweep['rule']}\nWrote {out}")
        return
    retrieval_results = await run_retrieval(questions, top_k=args.top_k, hybrid_bm25=args.hybrid_bm25)
    report: dict = {
        "n_questions": len(questions),
        "retrieval": summarize_retrieval(retrieval_results),
        "retrieval_detail": retrieval_results,
    }
    if args.with_llm:
        answer_results = await run_answers(questions)
        report["answers"] = summarize_answers(answer_results)
        report["answers_detail"] = answer_results

    out_dir = ROOT
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    write_markdown(report, out_dir / "report.md")
    print(json.dumps(report["retrieval"], indent=2))
    if args.with_llm:
        print(json.dumps(report["answers"], indent=2))
    print(f"\nWrote {out_dir / 'report.json'} and {out_dir / 'report.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-llm", action="store_true", help="also run answer-quality metrics (real API calls)")
    parser.add_argument("--hybrid-bm25", action="store_true", help="RRF-fuse a BM25 ranking in (P2-FR-03)")
    parser.add_argument(
        "--sweep-min-score", action="store_true", help="MIN_SCORE precision/recall/abstention curve (P2-FR-01)"
    )
    parser.add_argument("--top-k", type=int, default=10, help="retrieval depth to score against (default 10)")
    parser.add_argument("--limit", type=int, default=None, help="only run the first N questions (smoke runs)")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
