"""T-09: LangChain provider adapter - wire format, error mapping and cooldowns (SR-10, SR-11)."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.config import ProviderSpec
from core.generator import Generator
from core.llm import LLMError, invoke_chat, make_chat_model

SPEC = ProviderSpec("openrouter", "https://or.test/api/v1", "some/model:free", "sk-secret-123", {"X-Title": "Contexta"})
SOURCES = [{"id": "doc_001", "title": "RAG", "source": "local", "url": "https://arxiv.org/abs/1", "text": "RAG. x."}]


def _completion(content):
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}


def _call(handler, spec=SPEC):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = make_chat_model(spec, client, timeout=5)
    return asyncio.run(invoke_chat(model, spec, [HumanMessage("q")]))


def test_request_wire_format():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json=_completion("Answer [1]."))

    assert _call(handler) == "Answer [1]."
    assert seen["url"] == "https://or.test/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-secret-123"
    assert seen["headers"]["x-title"] == "Contexta"
    assert seen["body"]["model"] == "some/model:free" and seen["body"]["temperature"] == 0.2
    assert seen["body"]["messages"][0]["role"] == "user"


def test_extra_body_fields_are_sent():
    seen = {}
    spec = ProviderSpec("groq", "https://groq.test/v1", "openai/gpt-oss-20b", "k", extra={"reasoning_effort": "low"})

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_completion("ok"))

    _call(handler, spec)
    assert seen["body"]["reasoning_effort"] == "low"


def test_sdk_retries_are_disabled():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(LLMError):
        _call(handler)
    assert len(calls) == 1  # the provider chain, not the SDK, decides what happens next


@pytest.mark.parametrize(
    ("response", "message", "cooldown"),
    [
        (httpx.Response(401), "API key rejected", 600),
        (httpx.Response(403), "API key rejected", 600),
        (httpx.Response(404), "model 'some/model:free' not found (HTTP 404); check OPENROUTER_MODEL", 600),
        (httpx.Response(500), "HTTP 500", 30),
        (httpx.Response(503), "HTTP 503", 30),
        (httpx.Response(429, headers={"retry-after": "90"}), "rate limited", 90),
        (httpx.Response(429, headers={"retry-after": "900"}), "rate limited", 120),
        (httpx.Response(429), "rate limited", 20),
        (httpx.Response(200, json={"error": {"message": "upstream overloaded"}}), "malformed or error response", 30),
        (httpx.Response(200, content=b"<html>not json</html>"), "malformed or error response", 30),
        (httpx.Response(200, json=_completion("")), "empty response", 30),
        (httpx.Response(200, json=_completion("   ")), "empty response", 30),
    ],
)
def test_error_mapping(response, message, cooldown):
    with pytest.raises(LLMError) as exc:
        _call(lambda req: response)
    assert str(exc.value) == message and exc.value.cooldown == cooldown


@pytest.mark.parametrize(
    ("error", "message"),
    [(httpx.ReadTimeout("slow"), "timed out"), (httpx.ConnectError("refused"), "network error (ConnectError)")],
)
def test_transport_failures(error, message):
    def handler(req):
        raise error

    with pytest.raises(LLMError) as exc:
        _call(handler)
    assert str(exc.value) == message and exc.value.cooldown == 30


def test_content_blocks_are_joined():
    blocks = [{"type": "text", "text": "Part one "}, {"type": "text", "text": "[1]."}]
    assert _call(lambda req: httpx.Response(200, json=_completion(blocks))) == "Part one [1]."


def test_api_key_never_logged(caplog):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    gen = Generator((SPEC,), client)
    with caplog.at_level(logging.DEBUG):
        res = asyncio.run(gen.generate("q", SOURCES))
    assert not res.llm_used
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "sk-secret-123" not in logged and "sk-secret-123" not in repr(SPEC)


def test_cooldown_reported_by_available_provider_names():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(429)))
    gen = Generator((SPEC,), client)
    assert gen.available_provider_names == ["openrouter"]
    asyncio.run(gen.generate("q", SOURCES))
    assert gen.available_provider_names == [] and gen.provider_names == ["openrouter"]
