"""T-04 / T-05: liveness, readiness (FR-02, defect D-03) and proxy-aware rate limiting (D-06)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from core.vector_store import VectorStore


def test_ready_is_degraded_without_llm_and_lists_checks():
    with TestClient(create_app()) as c:
        r = c.get("/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert set(body["checks"]) == {"index", "embeddings", "llm", "arxiv", "database"}
        assert body["checks"]["index"]["ok"] and body["checks"]["embeddings"]["ok"]
        assert not body["checks"]["llm"]["ok"] and "extractive" in body["checks"]["llm"]["detail"]


def test_ready_reports_embedding_fallback():
    with TestClient(create_app()) as c:
        c.app.state.embedder.requested_backend = "fastembed"  # simulate: model failed to load
        body = c.get("/ready").json()
        assert body["status"] == "degraded"
        assert not body["checks"]["embeddings"]["ok"] and "hash fallback" in body["checks"]["embeddings"]["detail"]


def test_ready_is_503_when_index_empty():
    with TestClient(create_app()) as c:
        c.app.state.retriever.store = VectorStore(c.app.state.embedder.dim)
        r = c.get("/ready")
        assert r.status_code == 503 and r.json()["status"] == "not_ready"
        assert c.get("/health").status_code == 200  # liveness is unaffected


def test_ready_with_provider_and_no_secret_leak(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-very-secret-key-123")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "jwt-very-secret-456")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            ready, health = c.get("/ready"), c.get("/health")
            assert ready.json()["checks"]["llm"] == {"ok": True, "detail": "available: groq"}
            for r in (ready, health):
                assert "very-secret" not in r.text
    finally:
        get_settings.cache_clear()


def test_rate_limit_ignores_spoofed_forwarded_for():
    with TestClient(create_app()) as c:
        c.app.state.limiter.per_minute = 1
        body = {"question": "what is faiss", "use_arxiv": False}
        assert c.post("/ask", json=body, headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.9"}).status_code == 200
        # Same real client (right-most hop appended by the proxy), different spoofed left-most hop.
        r = c.post("/ask", json=body, headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.9"})
        assert r.status_code == 429
        # A genuinely different client still gets through.
        assert c.post("/ask", json=body, headers={"X-Forwarded-For": "10.0.0.10"}).status_code == 200


def test_forwarded_for_ignored_when_proxy_headers_untrusted(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            c.app.state.limiter.per_minute = 1
            body = {"question": "what is faiss", "use_arxiv": False}
            assert c.post("/ask", json=body, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
            assert c.post("/ask", json=body, headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 429
    finally:
        get_settings.cache_clear()


def test_onnx_runtime_telemetry_disabled_by_default():
    """Fresh interpreter without the variable: importing the embedder module must set it to 1."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    code = "import core.embeddings, os; print(os.environ['ORT_DISABLE_TELEMETRY'])"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1"
