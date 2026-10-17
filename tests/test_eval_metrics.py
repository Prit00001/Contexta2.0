"""Pure-math tests for eval/run_eval.py's scoring functions - no network, no LLM, no corpus."""

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
