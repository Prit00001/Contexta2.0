"""P2-FR-07: answer cache and /ask rate limit shared across instances through Redis, and /metrics.

Two independent app instances (separate processes in production, separate ``create_app()`` here)
point at one real ``redis-server`` started on a free port. Skipped when ``redis-server`` is not
installed. The LLM is a ``httpx.MockTransport`` - no network.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

QUESTION = "How does retrieval augmented generation reduce hallucination?"


def _llm(req: httpx.Request) -> httpx.Response:
    text = "Retrieval grounds the generator in retrieved passages [1]."
    return httpx.Response(
        200,
        json={"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]},
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def redis_url():
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server not installed")
    port = _free_port()
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"redis://127.0.0.1:{port}/0"
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def configure(monkeypatch):
    def apply(**values):
        monkeypatch.setenv("LLM_PROVIDERS", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()

    yield apply
    get_settings.cache_clear()


def _instance() -> TestClient:
    return TestClient(create_app(http_transport=httpx.MockTransport(_llm)))


def test_answer_cache_is_shared_between_instances(redis_url, configure):
    configure(REDIS_URL=redis_url)
    with _instance() as a, _instance() as b:
        first = a.post("/ask", json={"question": QUESTION, "use_arxiv": False}).json()
        assert first["llm_used"] and first["cached"] is False
        second = b.post("/ask", json={"question": QUESTION, "use_arxiv": False}).json()
        assert second["cached"] is True and second["answer"] == first["answer"]
        assert second["request_id"] != first["request_id"]


def test_rate_limit_is_shared_between_instances(redis_url, configure):
    configure(REDIS_URL=redis_url)
    with _instance() as a, _instance() as b:
        a.app.state.limiter.per_minute = b.app.state.limiter.per_minute = 1
        headers = {"X-Forwarded-For": "203.0.113.77"}
        body = {"question": "what is faiss used for", "use_arxiv": False}
        assert a.post("/ask", json=body, headers=headers).status_code == 200
        r = b.post("/ask", json=body, headers=headers)  # other instance, same client: over the limit
        assert r.status_code == 429 and r.headers["retry-after"] == "60"


def test_unreachable_redis_degrades_to_memory(configure):
    configure(REDIS_URL=f"redis://127.0.0.1:{_free_port()}/0")  # nothing listening
    with _instance() as c:
        c.app.state.limiter.per_minute = 1
        body = {"question": QUESTION, "use_arxiv": False}
        first = c.post("/ask", json=body)
        assert first.status_code == 200 and first.json()["llm_used"]
        assert c.post("/ask", json=body).status_code == 429  # in-memory window still enforced
        c.app.state.limiter.per_minute = 0
        assert c.post("/ask", json=body).json()["cached"] is True  # in-memory cache still works


def test_metrics_endpoint_counts_without_user_content(configure):
    configure()
    secret_question = "zebra-unique-marker how does dense passage retrieval work"
    with _instance() as c:
        assert c.post("/ask", json={"question": secret_question, "use_arxiv": False}).status_code == 200
        r = c.get("/metrics")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        text = r.text
        assert 'contexta_answers_total{cached="false",endpoint="ask",outcome="answered"}' in text
        assert 'contexta_workflow_stage_seconds_count{stage="retrieve",status="ok"}' in text
        assert 'contexta_llm_calls_total{provider="groq",status="ok"}' in text
        assert "zebra-unique-marker" not in text and "203.0.113" not in text


def test_metrics_can_be_disabled(configure):
    configure(METRICS_ENABLED="false")
    with _instance() as c:
        assert c.get("/metrics").status_code == 404
