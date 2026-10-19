"""P2-15 tracing: spans per request, workflow stage and LLM call, nested; no user content in them."""

from __future__ import annotations

import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.config import get_settings
from app.main import create_app
from core import tracing

QUESTION = "How does LoRA zqxmarker reduce trainable parameters?"
ANSWER = "LoRA trains small low-rank matrices instead of the full weights [1]."

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _sdk():
    # The global provider can be set once per process; spans from other tests just accumulate here.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_exporter))
        trace.set_tracer_provider(provider)
    yield


def _ask(monkeypatch, status: int = 200) -> tuple[dict, list]:
    def llm(req: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "unavailable"}})
        msg = {"role": "assistant", "content": ANSWER}
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})

    monkeypatch.setenv("LLM_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-zqx")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(http_transport=httpx.MockTransport(llm))) as c:
            _exporter.clear()
            body = c.post("/ask", json={"question": QUESTION, "use_arxiv": False}).json()
            spans = list(_exporter.get_finished_spans())
    finally:
        get_settings.cache_clear()
    return body, spans


def _by_name(spans, name):
    return [s for s in spans if s.name == name]


def test_request_stage_and_llm_spans_nest(monkeypatch):
    body, spans = _ask(monkeypatch)
    assert body["outcome"] == "answered"
    by_id = {s.context.span_id: s for s in spans}

    def ancestors(s):
        while s.parent is not None and s.parent.span_id in by_id:
            s = by_id[s.parent.span_id]
            yield s

    (request,) = [s for s in spans if s.attributes.get("contexta.request_id") == body["request_id"]]
    attrs = request.attributes  # FastAPI's server span records these itself; ours does without it
    assert attrs.get("http.route", attrs.get("contexta.route")) == "/ask"
    assert attrs.get("http.response.status_code", attrs.get("contexta.status_code")) == 200

    stages = [s for s in spans if s.name.startswith("workflow.")]
    assert [s.attributes["contexta.stage"] for s in sorted(stages, key=lambda s: s.start_time)] == [
        w["stage"] for w in body["workflow"]
    ]
    assert all(request in ancestors(s) for s in stages)  # FastAPI's own endpoint span sits in between
    assert {s.attributes["contexta.status"] for s in stages if s.name == "workflow.retrieve"} == {"ok"}

    (generate,) = _by_name(spans, "workflow.generate")
    (llm,) = _by_name(spans, "llm.call")
    assert llm.parent.span_id == generate.context.span_id
    assert llm.attributes["contexta.provider"] == "groq" and llm.attributes["contexta.status"] == "ok"


def test_spans_carry_no_user_content_or_credentials(monkeypatch):
    _, spans = _ask(monkeypatch)
    values = [str(v) for s in spans for v in s.attributes.values()]
    values += [s.name for s in spans]
    for secret in ("zqxmarker", "low-rank matrices", "test-key-zqx"):
        assert not any(secret in v for v in values), secret


def test_failed_llm_call_is_an_error_span(monkeypatch):
    body, spans = _ask(monkeypatch, status=503)
    assert body["outcome"] == "extractive_fallback"
    (llm,) = _by_name(spans, "llm.call")
    assert llm.status.status_code == StatusCode.ERROR and llm.attributes["contexta.status"] == "error"
    (generate,) = _by_name(spans, "workflow.generate")
    assert generate.attributes["contexta.status"] == "no_llm"


def test_configure_tracing_is_off_unless_enabled():
    assert tracing.configure_tracing(False) is False


def test_missing_sdk_never_blocks_startup(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    monkeypatch.setattr(tracing, "_provider", None)
    assert tracing.configure_tracing(True) is False
    assert "requirements-otel.txt" in caplog.text


def test_enabled_installs_an_otlp_exporter(monkeypatch):
    installed = []
    monkeypatch.setattr(tracing, "_provider", None)
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    try:
        assert tracing.configure_tracing(True, service_name="contexta-test") is True
        (provider,) = installed
        assert provider.resource.attributes["service.name"] == "contexta-test"
    finally:
        tracing.shutdown_tracing()  # stops the batch exporter thread; nothing was queued, so no network
        monkeypatch.setattr(tracing, "_provider", None)
