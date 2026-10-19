"""T-03 / FR-08: configuration is validated and fails clearly (defect D-04)."""

from __future__ import annotations

import pytest

from app.config import ConfigError, load_settings

PROVIDER_VARS = ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDERS")


@pytest.fixture(autouse=True)
def _no_providers(monkeypatch):
    for k in PROVIDER_VARS:
        monkeypatch.delenv(k, raising=False)


def test_defaults_load():
    s = load_settings()
    assert s.providers == () and s.llm_max_attempts == 2 and s.max_pdf_bytes == 4 * 1024 * 1024
    assert s.log_format == "text" and s.trust_proxy_headers is True


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("LLM_TIMEOUT", "abc"),
        ("LLM_TIMEOUT", "-1"),
        ("RATE_LIMIT_PER_MIN", "lots"),
        ("ARXIV_ENABLED", "maybe"),
        ("LLM_MAX_ATTEMPTS", "10"),
        ("LOG_FORMAT", "xml"),
        ("EMBED_BACKEND", "torch"),
        ("MIN_SCORE", "2"),
    ],
)
def test_invalid_values_fail_naming_the_variable_not_the_value(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert var in str(exc.value)
    if len(value) > 2:
        assert value not in str(exc.value)


def test_empty_values_are_treated_as_unset(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "")
    monkeypatch.setenv("DATABASE_URL", "")
    s = load_settings()
    assert s.rate_limit_per_min == 20 and s.database_url == ""


def test_unknown_provider_name_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq,openai")
    with pytest.raises(ConfigError, match="unknown provider"):
        load_settings()


def test_require_llm_without_provider_fails(monkeypatch):
    monkeypatch.setenv("REQUIRE_LLM", "true")
    with pytest.raises(ConfigError, match="REQUIRE_LLM"):
        load_settings()


def test_require_llm_with_provider_succeeds_and_respects_order(monkeypatch):
    monkeypatch.setenv("REQUIRE_LLM", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-123")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-456")
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter,groq")
    s = load_settings()
    assert [p.name for p in s.providers] == ["openrouter", "groq"]


def test_supabase_storage_requires_credentials(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "supabase")
    with pytest.raises(ConfigError, match="SUPABASE_URL"):
        load_settings()


def test_secrets_never_appear_in_repr(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-super-secret-value")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "jwt-super-secret-value")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:db-super-secret@h/db")
    text = repr(load_settings())
    assert "super-secret" not in text


def test_min_score_defaults_per_effective_backend(monkeypatch):
    from app.config import DEFAULT_MIN_SCORE, load_settings

    monkeypatch.delenv("MIN_SCORE", raising=False)
    s = load_settings()
    assert s.min_score is None
    assert s.min_score_for("fastembed") == DEFAULT_MIN_SCORE["fastembed"] == 0.225
    assert s.min_score_for("hash") == DEFAULT_MIN_SCORE["hash"] == 0.075
    monkeypatch.setenv("MIN_SCORE", "0.3")
    assert load_settings().min_score_for("hash") == 0.3  # explicit MIN_SCORE wins for every backend


def test_reranker_on_by_default_and_can_be_turned_off(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("RERANK_MODEL", raising=False)
    assert load_settings().rerank_model == "Xenova/ms-marco-MiniLM-L-6-v2"
    for off in ("none", "OFF", "false"):
        monkeypatch.setenv("RERANK_MODEL", off)
        assert load_settings().rerank_model == ""
