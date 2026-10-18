"""LLM provider access through LangChain (``langchain_openai.ChatOpenAI``).

Every provider in the chain speaks the OpenAI ``/chat/completions`` protocol, so one adapter
serves Groq, Gemini (OpenAI-compatible endpoint), OpenRouter, Ollama or any compatible server.

Two details matter:

* The OpenAI SDK used by ``ChatOpenAI`` is built on ``httpx2``, while the rest of the app shares
  one ``httpx.AsyncClient`` (connection pool, proxy settings, User-Agent). ``HttpxBridgeTransport``
  forwards the SDK's requests through that shared client, which also lets tests drive providers
  with ``httpx.MockTransport`` - the default suite makes no network calls.
* SDK retries are disabled (``max_retries=0``). Fallback between providers, cooldowns and the
  bounded regeneration loop are owned by ``core.generator`` and the LangGraph workflow; nested
  SDK retries would multiply latency on a failing free-tier provider.

SDK exceptions are mapped to ``LLMError(message, cooldown)`` with the same cooldown policy the
pre-LangChain implementation used (SRS SR-11).
"""

from __future__ import annotations

import json

import httpx
import httpx2
import openai
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from app.config import ProviderSpec

TEMPERATURE = 0.2
MAX_TOKENS = 1024  # leaves room for reasoning models (gpt-oss, Gemini 3.x) to think and still answer

_MODEL_ENV = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "openrouter": "OPENROUTER_MODEL", "custom": "LLM_MODEL"}
# The shared client has already decoded the body, so these must not be replayed to httpx2.
_DROP_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


class LLMError(Exception):
    """A provider call failed; ``cooldown`` is how long (s) to skip this provider."""

    def __init__(self, message: str, cooldown: float = 30.0):
        super().__init__(message)
        self.cooldown = cooldown


class HttpxBridgeTransport(httpx2.AsyncBaseTransport):
    """httpx2 transport that sends each request through an existing ``httpx.AsyncClient``."""

    def __init__(self, client: httpx.AsyncClient, timeout: float):
        self.client = client
        self.timeout = timeout

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        req = httpx.Request(
            request.method,
            str(request.url),
            headers=list(request.headers.multi_items()),
            content=body,
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()},
        )
        stream = _wants_stream(body)
        try:
            resp = await self.client.send(req, stream=stream)
        except httpx.TimeoutException as exc:
            raise httpx2.ReadTimeout(str(exc) or "timed out", request=request) from exc
        except httpx.HTTPError as exc:
            raise httpx2.ConnectError(type(exc).__name__, request=request) from exc
        headers = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in _DROP_RESPONSE_HEADERS]
        if stream:  # token streaming (POST /ask/stream): pass chunks through as they arrive
            return httpx2.Response(resp.status_code, headers=headers, stream=_BridgeStream(resp), request=request)
        return httpx2.Response(resp.status_code, headers=headers, content=resp.content, request=request)


def _wants_stream(body: bytes) -> bool:
    try:
        return bool(json.loads(body or b"{}").get("stream"))
    except (ValueError, AttributeError):
        return False


class _BridgeStream(httpx2.AsyncByteStream):
    """Decoded body of a streaming ``httpx`` response, re-exposed to the ``httpx2``-based SDK."""

    def __init__(self, resp: httpx.Response):
        self.resp = resp

    async def __aiter__(self):
        try:
            async for chunk in self.resp.aiter_bytes():
                yield chunk
        except httpx.TimeoutException as exc:
            raise httpx2.ReadTimeout(str(exc) or "timed out") from exc
        except httpx.HTTPError as exc:
            raise httpx2.ReadError(type(exc).__name__) from exc

    async def aclose(self) -> None:
        await self.resp.aclose()


def make_chat_model(spec: ProviderSpec, client: httpx.AsyncClient, timeout: float) -> ChatOpenAI:
    return ChatOpenAI(
        model=spec.model,
        base_url=spec.base_url,
        api_key=spec.api_key or "not-required",  # keyless custom endpoints (e.g. local Ollama)
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout=timeout,
        max_retries=0,
        default_headers=dict(spec.headers) or None,
        extra_body=dict(spec.extra) or None,
        http_async_client=httpx2.AsyncClient(transport=HttpxBridgeTransport(client, timeout)),
        http_socket_options=(),  # sync client is never used; keep its default proxy detection
    )


def _retry_after(exc: openai.APIStatusError) -> float:
    try:
        return float(exc.response.headers.get("retry-after", "0"))
    except (ValueError, AttributeError):
        return 0.0


def to_llm_error(exc: Exception, spec: ProviderSpec) -> LLMError:
    """Map an SDK/LangChain exception to an LLMError with the right cooldown."""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, openai.APITimeoutError):
        return LLMError("timed out", 30)
    if isinstance(exc, openai.RateLimitError):
        return LLMError("rate limited", min(max(_retry_after(exc), 20), 120))
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMError("API key rejected", 600)
    if isinstance(exc, openai.NotFoundError):  # a config problem, not capacity: usually a retired model ID
        env = _MODEL_ENV.get(spec.name, "the model setting")
        return LLMError(f"model '{spec.model}' not found (HTTP 404); check {env}", 600)
    if isinstance(exc, openai.APIStatusError):
        return LLMError(f"HTTP {exc.status_code}", 30)
    if isinstance(exc, openai.APIConnectionError):
        cause = exc.__cause__.__cause__ if exc.__cause__ is not None else None
        return LLMError(f"network error ({type(cause or exc).__name__})", 30)
    return LLMError("malformed or error response", 30)


async def invoke_chat(model: ChatOpenAI, spec: ProviderSpec, messages: list[BaseMessage]) -> str:
    """Call one provider and return the answer text, or raise LLMError."""
    try:
        msg = await model.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a classified LLMError
        raise to_llm_error(exc, spec) from exc
    content = msg.content
    if isinstance(content, list):  # some providers return content blocks
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    text = (content or "").strip()
    if not text:
        raise LLMError("empty response", 30)
    return text
