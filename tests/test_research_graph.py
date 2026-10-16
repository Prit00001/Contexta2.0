"""T-11: every LangGraph route, the retry bound and explicit error states (FR-15, SR-20..SR-24)."""

from __future__ import annotations

import asyncio

import pytest

from core.generator import GenResult
from core.lc_retriever import RetrievalResult
from core.prompts import RETRY_FEEDBACK
from core.sources import normalize_source
from services.research_graph import ALL_FAILED, NO_PROVIDER, build_research_graph, run_research

SOURCES = [
    normalize_source({"id": "doc_004", "source": "local", "title": "RAG", "url": "https://arxiv.org/abs/2005.11401",
                      "text": "RAG. Retrieval grounds answers in documents and reduces hallucination.", "score": 0.6}),
    normalize_source({"id": "doc_005", "source": "local", "title": "DPR", "url": "https://arxiv.org/abs/2004.04906",
                      "text": "DPR. Dense passage retrieval uses two BERT encoders for questions and passages.",
                      "score": 0.4}),
]  # fmt: skip


class FakeRetriever:
    def __init__(self, sources=SOURCES, warnings=(), error: Exception | None = None):
        self.sources, self.warnings, self.error, self.calls = list(sources), list(warnings), error, []

    async def aretrieve(self, question, top_k, **options):
        self.calls.append((question, top_k, options))
        if self.error:
            raise self.error
        return RetrievalResult(self.sources[:top_k], self.warnings, {"local": 1, "arxiv": 0})


class FakeGenerator:
    """Returns scripted answers; None in the script means 'no provider succeeded'."""

    def __init__(self, script, providers=("fake",)):
        self.script, self.providers, self.calls = list(script), providers, []

    async def generate(self, question, sources, feedback=None):
        self.calls.append(feedback)
        text = self.script.pop(0)
        if text is None:
            return GenResult("Extractive text [1]", "none", "extractive", False, ["fake: HTTP 503"])
        return GenResult(text, "fake", "fake-model", True, [], [])


def run(gen, retriever=None, max_attempts=2, question="Does retrieval reduce hallucination?", top_k=2, **kw):
    graph = build_research_graph(gen, max_attempts)
    return asyncio.run(run_research(graph, retriever or FakeRetriever(), question, top_k, **kw))


def stages(state):
    return [t["stage"] for t in state["trace"]]


def test_answered_path():
    gen = FakeGenerator(["Retrieval grounds answers [1]. DPR uses two encoders [2]."])
    s = run(gen)
    assert s["outcome"] == "answered" and s["llm_used"] and s["provider"] == "fake"
    assert stages(s) == ["validate_request", "retrieve", "generate", "validate_answer", "finalize"]
    assert s["citation_report"].valid_markers == 2 and gen.calls == [None]
    assert all(t["duration_ms"] >= 0 for t in s["trace"])


def test_question_whitespace_is_normalised_and_options_forwarded():
    retriever = FakeRetriever()
    run(FakeGenerator(["A [1]."]), retriever, question="  what   is RAG? ", options={"use_arxiv": False})
    assert retriever.calls == [("what is RAG?", 2, {"use_arxiv": False})]


def test_uncited_answer_is_retried_with_feedback_then_accepted():
    gen = FakeGenerator(["A fluent answer with no citations.", "Now cited [1]."])
    s = run(gen)
    assert s["outcome"] == "answered" and s["attempts"] == 2 and s["answer"] == "Now cited [1]."
    assert gen.calls == [None, RETRY_FEEDBACK]
    assert [t["status"] for t in s["trace"] if t["stage"] == "validate_answer"] == ["retry", "ok"]


def test_retries_are_bounded_then_extractive_fallback():
    gen = FakeGenerator(["no cites", "still none", "never reached"])
    s = run(gen, max_attempts=2)
    assert len(gen.calls) == 2 and s["attempts"] == 2
    assert s["outcome"] == "extractive_fallback" and not s["llm_used"] and s["model"] == "extractive"
    assert any("no valid citations after 2 attempt(s)" in d for d in s["diagnostics"])
    assert s["citation_report"].valid_markers >= 1  # the extractive answer cites its sources


def test_single_attempt_configuration_never_retries():
    gen = FakeGenerator(["no cites", "unused"])
    s = run(gen, max_attempts=1)
    assert len(gen.calls) == 1 and s["outcome"] == "extractive_fallback"


@pytest.mark.parametrize("requested", [0, 99])
def test_attempt_bound_is_clamped(requested):
    gen = FakeGenerator(["x"] * 5)
    run(gen, max_attempts=requested)
    assert 1 <= len(gen.calls) <= 3


def test_hallucinated_citations_alone_make_answer_unusable():
    gen = FakeGenerator(["Claim [7].", "Claim [1]. Other [9]."])
    s = run(gen)
    assert s["outcome"] == "answered" and s["answer"] == "Claim [1]. Other."
    assert s["citation_report"].removed_markers == [9]


@pytest.mark.parametrize(("providers", "note"), [(("fake",), ALL_FAILED), ((), NO_PROVIDER)])
def test_no_llm_goes_to_fallback(providers, note):
    gen = FakeGenerator([None], providers=providers)
    s = run(gen)
    assert stages(s) == ["validate_request", "retrieve", "generate", "fallback", "finalize"]
    assert s["outcome"] == "extractive_fallback" and s["answer"] == "Extractive text [1]"
    assert note in s["diagnostics"] and "fake: HTTP 503" in s["diagnostics"]


def test_empty_retrieval_skips_generation():
    gen = FakeGenerator([])
    s = run(gen, FakeRetriever(sources=[]), empty_message="Nothing found.")
    assert stages(s) == ["validate_request", "retrieve", "insufficient_evidence", "finalize"]
    assert s["outcome"] == "insufficient_evidence" and s["answer"] == "Nothing found." and gen.calls == []
    assert s["citation_report"].citations == []


def test_retrieval_warnings_are_propagated():
    s = run(FakeGenerator(["A [1]."]), FakeRetriever(warnings=["arXiv down"]))
    assert s["warnings"] == ["arXiv down"]


def test_retriever_exception_becomes_explicit_error_state():
    gen = FakeGenerator([])
    s = run(gen, FakeRetriever(error=RuntimeError("index corrupted")))
    assert s["outcome"] == "error" and s["error"] == "retrieval_failed" and gen.calls == []
    assert stages(s) == ["validate_request", "retrieve", "finalize"]
    assert "index corrupted" not in str(s["diagnostics"])  # no internal detail leaks to clients


@pytest.mark.parametrize(
    ("question", "top_k"), [("hi", 2), ("x" * 1001, 2), ("valid question", 0), ("valid question", 11)]
)
def test_invalid_requests_stop_before_retrieval(question, top_k):
    retriever, gen = FakeRetriever(), FakeGenerator([])
    s = run(gen, retriever, question=question, top_k=top_k)
    assert s["outcome"] == "error" and retriever.calls == [] and gen.calls == []
    assert stages(s) == ["validate_request", "finalize"]


def test_graph_structure_matches_design():
    graph = build_research_graph(FakeGenerator([]), 2).get_graph()
    nodes = set(graph.nodes) - {"__start__", "__end__"}
    assert nodes == {"validate_request", "retrieve", "generate", "validate_answer", "fallback",
                     "insufficient_evidence", "finalize"}  # fmt: skip
    edges = {(e.source, e.target) for e in graph.edges}
    assert ("validate_answer", "generate") in edges  # the bounded retry loop
    assert ("retrieve", "insufficient_evidence") in edges and ("generate", "fallback") in edges
