"""The /workspace page and its public sign-in config (no database needed)."""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_workspace_page_is_served():
    with TestClient(create_app()) as c:
        r = c.get("/workspace")
    assert r.status_code == 200 and "Contexta Workspace" in r.text


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
