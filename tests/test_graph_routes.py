"""Every route through the research graph, node by node: the exact (stage, status) trace and the
final outcome. test_research_graph.py checks behaviour per route; this table pins the statuses each
node reports (which feed /metrics and the stream's "stage" events) so a silent rerouting shows up."""

from __future__ import annotations

import asyncio

import pytest

from core.generator import GenResult, extractive_answer
from services.research_graph import build_research_graph, run_research
from tests.test_research_graph import SOURCES, FakeRetriever

WEAK = [{**s, "score": 0.2} for s in SOURCES]  # below the selective rewrite gap
HISTORY = [{"question": "What is RAG?", "answer": "Retrieval-augmented generation [1]."}]


class ScriptedGenerator:
    """``generate`` answers come from ``answers`` (None = no provider succeeded); ``complete`` calls
    (follow-up, rewrite, claim judge) from ``completions`` in order (None = no provider answered)."""

    def __init__(self, answers=(), completions=(), providers=("fake",)):
        self.answers, self.completions, self.providers = list(answers), list(completions), providers
        self.complete_calls = 0

    async def generate(self, question, sources, feedback=None):
        text = self.answers.pop(0)
        if text is None:
            # what core.generator.Generator returns when no provider answers
            return GenResult(extractive_answer(question, sources), "none", "extractive", False, ["fake: HTTP 503"])
        return GenResult(text, "fake", "fake-model", True, [], [])

    async def complete(self, messages):
        self.complete_calls += 1
        return self.completions.pop(0), []


CITED = "Retrieval grounds answers in documents and reduces hallucination [1]."
OK, ANSWERED = ("validate_request", "ok"), ("finalize", "answered")
RETRIEVE, GENERATE = ("retrieve", "ok"), ("generate", "ok")

ROUTES = {
    "invalid request": (
        dict(question="hi"),
        [("validate_request", "invalid"), ("finalize", "error")],
        "error",
    ),
    "nothing retrieved": (
        dict(retriever=FakeRetriever(sources=[])),
        [OK, ("retrieve", "empty"), ("insufficient_evidence", "ok"), ("finalize", "insufficient_evidence")],
        "insufficient_evidence",
    ),
    "retrieval error": (
        dict(retriever=FakeRetriever(error=RuntimeError("index down"))),
        [OK, ("retrieve", "error"), ("finalize", "error")],
        "error",
    ),
    "answered": (
        dict(answers=[CITED]),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "retry then answered": (
        dict(answers=["No citations here.", CITED]),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "retry"), GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "unusable twice -> fallback": (
        dict(answers=["No citations.", "Still none."]),
        [
            OK,
            RETRIEVE,
            GENERATE,
            ("validate_answer", "retry"),
            GENERATE,
            ("validate_answer", "unusable"),
            ("fallback", "ok"),
            ("finalize", "extractive_fallback"),
        ],
        "extractive_fallback",
    ),  # fmt: skip
    "no provider -> fallback": (
        dict(answers=[None]),
        [OK, RETRIEVE, ("generate", "no_llm"), ("fallback", "ok"), ("finalize", "extractive_fallback")],
        "extractive_fallback",
    ),
    "model abstains": (
        dict(answers=["The provided sources do not contain enough information to answer this."]),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "abstained"), ("finalize", "insufficient_evidence")],
        "insufficient_evidence",
    ),
    "follow-up contextualised": (
        dict(answers=[CITED], completions=['{"question": "Does RAG reduce hallucination?"}'], history=HISTORY),
        [OK, ("contextualize", "ok"), RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "follow-up without LLM": (
        dict(answers=[CITED], completions=[None], history=HISTORY),
        [OK, ("contextualize", "fallback"), RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "rewrite first": (
        dict(answers=[CITED], completions=['{"queries": ["RAG hallucination"]}'], rewrite_mode="multi"),
        [OK, ("rewrite", "ok"), RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "rewrite fails open": (
        dict(answers=[CITED], completions=[None], rewrite_mode="decompose"),
        [OK, ("rewrite", "failed"), RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "selective, strong retrieval": (
        dict(answers=[CITED], rewrite_mode="selective", rewrite_gap_score=0.3),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),
    "selective, weak retrieval": (
        dict(
            answers=[CITED],
            completions=['{"queries": ["RAG hallucination"]}'],
            rewrite_mode="selective",
            retriever=FakeRetriever(sources=WEAK),
            rewrite_gap_score=0.3,
        ),
        [OK, RETRIEVE, ("rewrite", "ok"), RETRIEVE, GENERATE, ("validate_answer", "ok"), ANSWERED],
        "answered",
    ),  # fmt: skip
    "claim check, quote-supported": (
        dict(answers=[CITED], claim_check=True),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "ok"), ("check_claims", "ok"), ANSWERED],
        "answered",
    ),
    "claim check, judge unavailable": (
        dict(answers=["DPR was trained on Wikipedia by Facebook [2]."], completions=[None], claim_check=True),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "ok"), ("check_claims", "judge_unavailable"), ANSWERED],
        "answered",
    ),
    "claim check after fallback": (
        dict(answers=[None], claim_check=True),
        [
            OK,
            RETRIEVE,
            ("generate", "no_llm"),
            ("fallback", "ok"),
            ("check_claims", "ok"),
            ("finalize", "extractive_fallback"),
        ],
        "extractive_fallback",
    ),  # fmt: skip
    "claim check skipped on abstention": (
        dict(answers=["The sources do not contain information about that."], claim_check=True),
        [OK, RETRIEVE, GENERATE, ("validate_answer", "abstained"), ("finalize", "insufficient_evidence")],
        "insufficient_evidence",
    ),
}


@pytest.mark.parametrize("route", ROUTES)
def test_route_trace(route):
    kw, expected, outcome = ROUTES[route]
    kw = dict(kw)
    gen = ScriptedGenerator(kw.pop("answers", ()), kw.pop("completions", ()))
    graph = build_research_graph(
        gen,
        max_attempts=2,
        claim_check=kw.pop("claim_check", False),
        rewrite_mode=kw.pop("rewrite_mode", "off"),
        rewrite_gap_score=kw.pop("rewrite_gap_score", 0.35),
    )
    retriever = kw.pop("retriever", None) or FakeRetriever()
    state = asyncio.run(
        run_research(graph, retriever, kw.pop("question", "Does retrieval reduce hallucination?"), 2, **kw)
    )
    assert [(t["stage"], t["status"]) for t in state["trace"]] == expected
    assert state["outcome"] == outcome
    assert not gen.answers and not gen.completions  # every scripted LLM call was made, none extra


def test_extractive_fallback_needs_no_judge_call():
    """Extractive answers quote their sources, so claim checking them must stay local: it runs when
    no provider is answering, and a judge call would only fail again."""
    gen = ScriptedGenerator([None])
    graph = build_research_graph(gen, claim_check=True)
    state = asyncio.run(run_research(graph, FakeRetriever(), "Does retrieval reduce hallucination?", 2))
    assert gen.complete_calls == 0
    claims = state["citation_report"].claims
    assert claims and all(c["support"] == "supported" and c["support_method"] == "quote" for c in claims)


def test_every_node_is_reached_by_some_route():
    reached = {stage for _, expected, _ in ROUTES.values() for stage, _ in expected}
    graph = build_research_graph(ScriptedGenerator(), claim_check=True, rewrite_mode="selective")
    assert reached == set(graph.get_graph().nodes) - {"__start__", "__end__"}
