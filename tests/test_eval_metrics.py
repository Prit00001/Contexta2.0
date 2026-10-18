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
    off = {r["id"]: r for r in report["conditions"]["off/hybrid"]["detail"]}
    for b in baseline:  # condition "off" reproduces the plain retrieval baseline exactly
        assert off[b["id"]]["ranked"] == b["ranked"] and off[b["id"]]["recall@5"] == b["recall@5"]
    n = len(questions)
    assert report["conditions"]["off/hybrid"]["summary"]["cost"]["llm_calls"] == 0
    assert report["conditions"]["multi/hybrid"]["summary"]["cost"]["llm_calls"] == n
    assert report["conditions"]["selective/hybrid"]["summary"]["cost"]["llm_calls"] == n
    assert all(
        r["queries"][0] == q["question"]
        for r, q in zip(report["conditions"]["multi/hybrid"]["detail"], questions, strict=True)
    )


def test_decoys_are_deterministic_and_counted_offline():
    import asyncio

    import rewrite_eval
    from run_eval import load_questions

    questions = load_questions(limit=8)
    decoys = rewrite_eval.make_decoys(questions)
    assert decoys == rewrite_eval.make_decoys(questions)  # no randomness, no LLM
    assert {rewrite_eval.decoy_kind(d["id"]) for d in decoys} == set(rewrite_eval.DECOY_KINDS)
    report = asyncio.run(rewrite_eval.run_rewrite_comparison(questions, ["off"], list(rewrite_eval.RETRIEVERS), True))
    assert report["decoys"] == len(decoys)
    assert set(report["conditions"]) == {f"off/{k}" for k in rewrite_eval.RETRIEVERS}
    for data in report["conditions"].values():
        s = data["summary"]
        assert 0.0 <= s["overall"]["decoy_win"] <= 1.0 and set(s["decoys"]) == set(rewrite_eval.DECOY_KINDS)
        assert s["cost"]["llm_calls"] == 0


def test_claim_sheet_round_trip(tmp_path):
    import claim_labels

    from core.citations import validate_citations

    sources = [{"text": "LoRA freezes the weights."}, {"text": "QLoRA quantizes to 4 bits."}]
    report = validate_citations("LoRA freezes the weights [1]. QLoRA is faster [2]. No cite here.", sources)
    report.claims[0].update(support="supported", support_method="quote")
    report.claims[1].update(support="not_supported", support_method="llm_judge")
    rows = claim_labels.claim_rows({"id": "q1", "question": "q?"}, {"citation_report": report, "sources": sources})
    assert [r["claim_index"] for r in rows] == [0, 1]  # the uncited claim is not in the sheet
    assert rows[1]["cited_text"] == "[2] QLoRA quantizes to 4 bits." and rows[1]["human_label"] == ""

    rows[0]["human_label"], rows[1]["human_label"] = "supported", "supported"  # human disagrees on row 2
    sheet = tmp_path / "claims.csv"
    claim_labels.write_sheet(rows, sheet)
    scores = claim_labels.score_sheet(sheet)
    assert scores["overall"] == {
        "n_labelled": 2, "coverage": 1.0, "accuracy": 0.5, "precision_supported": 1.0,
        "recall_not_supported": None, "human_not_supported": 0,
    }  # fmt: skip
    assert scores["llm_judge"]["accuracy"] == 0.0 and scores["quote"]["accuracy"] == 1.0
    assert "only 2 rows labelled" in claim_labels.markdown(scores).lower()


def test_rewrite_cache_saves_quota_but_reports_production_cost(monkeypatch, tmp_path):
    import asyncio

    import rewrite_eval
    from run_eval import load_questions

    from app.config import get_settings

    calls = []

    class CountingRewriter:
        def __init__(self, *args, **kwargs):
            pass

        async def complete(self, messages):
            calls.append(1)
            return '{"queries": ["low-rank adaptation"]}', []

    monkeypatch.setattr(rewrite_eval, "Generator", CountingRewriter)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("REWRITE_GAP_SCORE", "1")  # selective always fires
    get_settings.cache_clear()
    cache = tmp_path / "rewrites.json"
    try:
        questions = load_questions(limit=5)
        first = asyncio.run(rewrite_eval.run_rewrite_comparison(questions, ["multi", "selective"], cache_path=cache))
        assert len(calls) == 5  # selective reused multi's rewrites (same prompt)
        second = asyncio.run(rewrite_eval.run_rewrite_comparison(questions, ["multi"], cache_path=cache))
    finally:
        get_settings.cache_clear()
    assert len(calls) == 5  # second run: all from cache
    for report in (first, second):
        assert report["conditions"]["multi/hybrid"]["summary"]["cost"]["llm_calls"] == 5
    assert first["conditions"]["selective/hybrid"]["summary"]["cost"]["llm_calls"] == 5
