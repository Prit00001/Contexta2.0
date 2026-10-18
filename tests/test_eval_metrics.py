"""Tests for eval/run_eval.py: pure-math scoring functions, plus the MIN_SCORE sweep on hash embeddings (offline)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
from run_eval import ndcg_at_k, recall_at_k, reciprocal_rank  # noqa: E402


def test_recall_at_k_single_gold_hit_and_miss():
    assert recall_at_k(["a", "b", "c"], {"b"}, 3) == 1.0
    assert recall_at_k(["a", "b", "c"], {"z"}, 3) == 0.0
    assert recall_at_k(["a", "b", "c"], {"c"}, 1) == 0.0  # found, but outside k=1


def test_recall_at_k_multi_gold_is_fractional():
    # Only one of two gold docs makes the top 1 - this is the "comparison" ceiling-effect case.
    assert recall_at_k(["a", "x", "b"], {"a", "b"}, 1) == 0.5
    assert recall_at_k(["a", "x", "b"], {"a", "b"}, 3) == 1.0


def test_recall_at_k_empty_gold_is_zero():
    assert recall_at_k(["a", "b"], set(), 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["z", "a", "b"], {"a"}) == 0.5
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0
    assert reciprocal_rank(["z", "y"], {"a"}) == 0.0


def test_ndcg_at_k_perfect_ranking_is_one():
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 10) == 1.0


def test_ndcg_at_k_no_gold_in_results_is_zero():
    assert ndcg_at_k(["x", "y"], {"a"}, 10) == 0.0


def test_ndcg_at_k_rewards_earlier_hits():
    high = ndcg_at_k(["a", "x", "y"], {"a"}, 10)
    low = ndcg_at_k(["x", "a", "y"], {"a"}, 10)
    assert high > low


def test_min_score_sweep_curve_is_monotonic():
    import asyncio

    from run_eval import load_questions, run_score_sweep

    sweep = asyncio.run(run_score_sweep(load_questions()))  # conftest: hash embeddings, offline
    curve = sweep["curve"]
    assert curve[0]["min_score"] == 0.0 and curve[0]["false_abstention_rate"] == 0.0
    for a, b in zip(curve, curve[1:], strict=False):  # a higher threshold never retrieves more
        assert b["recall@5"] <= a["recall@5"]
        assert b["abstention_rate_unanswerable"] >= a["abstention_rate_unanswerable"]
    assert sweep["recommended_min_score"] in {c["min_score"] for c in curve}


def test_rewrite_comparison_off_matches_baseline_and_counts_calls(monkeypatch):
    import asyncio

    import rewrite_eval
    from run_eval import load_questions, run_retrieval

    from app.config import get_settings

    class FakeRewriter:
        def __init__(self, *args, **kwargs):
            pass

        async def complete(self, messages):
            return '{"queries": ["low-rank adaptation parameter-efficient fine-tuning"]}', []

    monkeypatch.setattr(rewrite_eval, "Generator", FakeRewriter)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("REWRITE_GAP_SCORE", "1")  # selective always fires
    get_settings.cache_clear()
    try:
        questions = load_questions(limit=12)
        report = asyncio.run(rewrite_eval.run_rewrite_comparison(questions, ["off", "multi", "selective"]))
        baseline = asyncio.run(run_retrieval(questions))
    finally:
        get_settings.cache_clear()
    off = {r["id"]: r for r in report["conditions"]["off"]["detail"]}
    for b in baseline:  # condition "off" reproduces the plain retrieval baseline exactly
        assert off[b["id"]]["ranked"] == b["ranked"] and off[b["id"]]["recall@5"] == b["recall@5"]
    n = len(questions)
    assert report["conditions"]["off"]["summary"]["cost"]["llm_calls"] == 0
    assert report["conditions"]["multi"]["summary"]["cost"]["llm_calls"] == n
    assert report["conditions"]["selective"]["summary"]["cost"]["llm_calls"] == n
    assert all(
        r["queries"][0] == q["question"]
        for r, q in zip(report["conditions"]["multi"]["detail"], questions, strict=True)
    )
