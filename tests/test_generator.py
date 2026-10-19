import asyncio

import httpx

from app.config import ProviderSpec
from core.generator import Generator, extractive_answer

SOURCES = [
    {"title": "RAG", "source": "local", "url": "u", "text": "RAG. Retrieval grounds answers in documents. It reduces hallucination."},
    {"title": "DPR", "source": "arxiv", "url": "u2", "text": "DPR. Dense retrieval beats BM25."},
]


def make(handler, *names):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    specs = tuple(ProviderSpec(n, f"https://{n}.test/v1", "m", "key") for n in names)
    return Generator(specs, client)


def ok(text):
    # Spec-conformant OpenAI chat completion (real providers always include role).
    return httpx.Response(200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]})


def test_falls_back_to_next_provider_on_429():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "5"}) if "groq" in req.url.host else ok("Answer [1] and bogus [9].")

    gen = make(handler, "groq", "gemini")
    res = asyncio.run(gen.generate("q", SOURCES))
    assert res.llm_used and res.provider == "gemini"
    assert "[9]" not in res.answer and "[1]" in res.answer  # hallucinated citation stripped
    assert any("groq" in w for w in res.warnings)


def test_cooldown_skips_failed_provider():
    calls = []

    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(500) if "groq" in req.url.host else ok("fine [1]")

    gen = make(handler, "groq", "gemini")
    asyncio.run(gen.generate("q", SOURCES))
    asyncio.run(gen.generate("q", SOURCES))
    assert calls.count("groq.test") == 1  # second request skipped the cooling-down provider


def test_all_providers_fail_returns_extractive():
    gen = make(lambda req: httpx.Response(503), "groq", "gemini")
    res = asyncio.run(gen.generate("what reduces hallucination?", SOURCES))
    assert not res.llm_used and res.model == "extractive" and "[1]" in res.answer


def test_extractive_picks_relevant_sentence():
    assert "hallucination" in extractive_answer("does retrieval reduce hallucination", SOURCES)
