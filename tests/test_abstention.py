"""LLM-level abstention: an uncited 'the sources don't cover this' answer is insufficient_evidence,
not a citation failure to retry and replace with irrelevant extractive quotes (EVALUATION_REPORT 5e)."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from core.abstention import ABSTAIN_PHRASE, is_abstention

REAL = "I’m sorry, but none of the provided sources contain information about Generative Adversarial Networks."


@pytest.mark.parametrize(
    "text",
    [
        REAL,  # verbatim gpt-oss-20b output on eval question q112
        f"{ABSTAIN_PHRASE} to answer this question.",
        "The sources do not discuss diffusion models.",
        "There is not enough information in the excerpts to say.",
        "These documents don't mention reinforcement learning from human feedback.",
    ],
)
def test_recognised_abstentions(text):
    assert is_abstention(text, valid_citations=0)


@pytest.mark.parametrize(
    "text",
    [
        "LoRA freezes the pretrained weights and trains low-rank matrices.",  # uncited but a real answer
        "GANs train a generator against a discriminator.",
        "",
    ],
)
def test_non_abstentions(text):
    assert not is_abstention(text, valid_citations=0)


def test_cited_answer_is_never_an_abstention():
    assert not is_abstention("The sources do not cover cost, but LoRA trains fewer parameters [1].", valid_citations=1)


def _client(monkeypatch, reply: str, calls: list) -> TestClient:
    def llm(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        msg = {"role": "assistant", "content": reply}
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})

    monkeypatch.setenv("LLM_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    return TestClient(create_app(http_transport=httpx.MockTransport(llm)))


def test_llm_abstention_is_returned_without_retry_or_fallback(monkeypatch):
    calls: list = []
    try:
        with _client(monkeypatch, REAL, calls) as c:
            d = c.post("/ask", json={"question": "How are GANs trained?", "use_arxiv": False}).json()
    finally:
        get_settings.cache_clear()
    assert d["outcome"] == "insufficient_evidence" and d["answer"] == REAL
    assert d["llm_used"] is True and d["provider"] == "groq" and len(calls) == 1  # no regeneration
    assert [w["stage"] for w in d["workflow"]][-2:] == ["validate_answer", "finalize"]
    assert d["workflow"][-2]["status"] == "abstained"


def test_uncited_non_abstention_still_retries_then_falls_back(monkeypatch):
    calls: list = []
    try:
        with _client(monkeypatch, "GANs train a generator against a discriminator.", calls) as c:
            d = c.post("/ask", json={"question": "How are GANs trained?", "use_arxiv": False}).json()
    finally:
        get_settings.cache_clear()
    assert d["outcome"] == "extractive_fallback" and len(calls) == 2
