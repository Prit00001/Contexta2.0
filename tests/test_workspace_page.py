"""The /workspace page and its public sign-in config (no database needed)."""

from fastapi.testclient import TestClient

import app.main as app_main
from app.config import get_settings
from app.main import create_app


def test_static_pages_are_served_when_the_react_app_is_not_built(monkeypatch, tmp_path):
    monkeypatch.setattr(app_main, "FRONTEND_DIST", tmp_path / "missing")
    with TestClient(create_app()) as c:
        assert "Contexta Workspace" in c.get("/workspace").text
        assert "Cited answers from ML/NLP papers" in c.get("/").text


def test_react_app_is_served_for_its_routes(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root>spa</div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(app_main, "FRONTEND_DIST", tmp_path)
    with TestClient(create_app()) as c:
        for path in ("/", "/workspace", "/workspace/abc/ask/def"):
            r = c.get(path)
            assert r.status_code == 200 and "spa" in r.text, path
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/health").json()["status"]  # API routes are not swallowed by the SPA
        assert c.get("/workspaces").status_code in (401, 503)


def test_auth_config_without_database_or_sign_in(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")  # CI sets one for the integration tests
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            d = c.get("/auth/config").json()
    finally:
        get_settings.cache_clear()
    assert d == {"workspaces_enabled": False, "supabase_url": None, "supabase_anon_key": None,
                 "history_retention_days": 30}  # fmt: skip


def test_auth_config_exposes_only_public_sign_in_values(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-secret-zqx")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "jwt-secret-zqx")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            r = c.get("/auth/config")
    finally:
        get_settings.cache_clear()
    d = r.json()
    assert d["supabase_url"] == "https://example.supabase.co" and d["supabase_anon_key"] == "public-anon-key"
    assert "zqx" not in r.text  # never the service key or JWT secret


def test_anon_key_alone_does_not_enable_sign_in(monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            d = c.get("/auth/config").json()
    finally:
        get_settings.cache_clear()
    assert d["supabase_url"] is None and d["supabase_anon_key"] is None
