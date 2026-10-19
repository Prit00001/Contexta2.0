"""T-13 / T-14: request IDs, structured logs without user content or secrets, safe 500s, OpenAPI examples."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.observability import configure_logging, redact

QUESTION = "How does retrieval-augmented generation work?"


@pytest.fixture
def json_logs(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    configure_logging("INFO", "text")


def _records(stderr: str) -> list[dict]:
    out = []
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("{"):
            out.append(json.loads(line))
    return out


def test_request_id_header_and_body_match():
    with TestClient(create_app()) as c:
        r = c.post("/ask", json={"question": QUESTION, "use_arxiv": False})
        assert r.status_code == 200
        rid = r.headers["x-request-id"]
        assert len(rid) == 16 and r.json()["request_id"] == rid
        assert c.get("/health").headers["x-request-id"] != rid


def test_safe_incoming_request_id_is_propagated_unsafe_is_replaced():
    with TestClient(create_app()) as c:
        assert c.get("/health", headers={"X-Request-ID": "client-abc.123"}).headers["x-request-id"] == "client-abc.123"
        replaced = c.get("/health", headers={"X-Request-ID": "bad id\r\nSet-Cookie: x"}).headers["x-request-id"]
        assert replaced != "bad id" and "\n" not in replaced and len(replaced) == 16


def test_json_logs_cover_request_workflow_and_result_without_question_text(json_logs, capsys, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecretvalue123456")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.generator.providers = ()  # keep the key configured but never call out
        rid = c.post("/ask", json={"question": QUESTION, "use_arxiv": False}).headers["x-request-id"]
    err = capsys.readouterr().err
    recs = _records(err)
    assert recs, "expected JSON log lines"
    mine = [r for r in recs if r.get("request_id") == rid]
    events = {r.get("event") for r in mine}
    assert {"http.request", "workflow.stage", "ask.result"} <= events
    http = next(r for r in mine if r["event"] == "http.request")
    assert http["status_code"] == 200 and isinstance(http["duration_ms"], int) and http["path"] == "/ask"
    stages = [r["stage"] for r in mine if r["event"] == "workflow.stage"]
    assert stages[:2] == ["validate_request", "retrieve"] and stages[-1] == "finalize"
    result = next(r for r in mine if r["event"] == "ask.result")
    assert result["question_len"] == len(QUESTION) and len(result["question_sha8"]) == 8
    assert result["outcome"] == "extractive_fallback" and result["category"] == "llm_unavailable"
    assert QUESTION not in err and "retrieval-augmented generation work" not in err
    assert "supersecretvalue" not in err


def test_validation_and_rate_limit_categories_logged(json_logs, capsys):
    with TestClient(create_app()) as c:
        c.post("/ask", json={"question": "hi"})
        c.app.state.limiter.per_minute = 1
        c.post("/ask", json={"question": "what is faiss", "use_arxiv": False})
        c.post("/ask", json={"question": "what is faiss", "use_arxiv": False})
    http = [r for r in _records(capsys.readouterr().err) if r.get("event") == "http.request"]
    assert [r["category"] for r in http if r["status_code"] in (422, 429)] == ["validation", "rate_limited"]


def test_unhandled_exception_returns_generic_500_with_request_id(capsys):
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("database password is hunter2")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error" and body["request_id"] == r.headers["x-request-id"]
    assert "hunter2" not in r.text and "Traceback" not in r.text


@pytest.mark.parametrize(
    "secret",
    [
        "sk-or-v1-abcdef0123456789",
        "gsk_ABCDEFGH12345678",
        "AIzaSyA1234567890abcdefghijkl",
        "Bearer eyJhbGciOiJIUzI1NiJ9.x.y",
        "postgresql://user:pw@db:5432/x",
        "cxk_abcdefghijklmnop",
    ],
)
def test_redaction_backstop(secret):
    assert secret not in redact(f"failed with {secret} in message")


def test_openapi_has_examples_and_error_responses():
    with TestClient(create_app()) as c:
        spec = c.get("/openapi.json").json()
    ask = spec["paths"]["/ask"]["post"]
    assert {"200", "422", "429", "500", "503"} <= set(ask["responses"])
    assert ask["responses"]["200"]["content"]["application/json"]["example"]["outcome"] == "answered"
    req_schema = spec["components"]["schemas"]["AskRequest"]
    assert req_schema["examples"][0]["question"].startswith("How does")
    assert "/ready" in spec["paths"] and "503" in spec["paths"]["/ready"]["get"]["responses"]
