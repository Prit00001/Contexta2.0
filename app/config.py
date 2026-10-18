"""Central configuration, read once from environment variables (and an optional .env file).

Values are validated by Pydantic at startup. A malformed value (e.g. ``LLM_TIMEOUT=abc``) or an
impossible combination (``REQUIRE_LLM=true`` with no provider) raises ``ConfigError`` naming the
variable - the previous implementation silently fell back to defaults (defect D-04, FR-08).
Empty variables (``DATABASE_URL=`` as in .env.example) are treated as unset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


class ConfigError(RuntimeError):
    """Raised at startup when the environment configuration is invalid."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)  # never shown in repr/logs
    headers: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)  # extra JSON fields for /chat/completions


KNOWN_PROVIDERS = ("groq", "gemini", "openrouter", "custom")

# Default MIN_SCORE per *effective* embedding backend (P2-FR-01), used when MIN_SCORE is unset.
# hash: calibrated with `eval/run_eval.py --sweep-min-score` (EVALUATION_REPORT.md section 5b) - the old
# 0.2 left 26% of answerable eval questions with no sources at all on hash embeddings.
# fastembed (MiniLM): 0.2 is the Phase 1 informal value; not yet swept (model not downloadable here).
DEFAULT_MIN_SCORE = {"fastembed": 0.2, "hash": 0.075}

# field name -> environment variable (only where they differ from FIELD.upper())
_ENV_NAMES = {
    "model_cache_dir": "FASTEMBED_CACHE_PATH",
    "site_url": "APP_SITE_URL",
    "llm_provider_order": "LLM_PROVIDERS",
}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # embeddings / retrieval
    embed_backend: Literal["fastembed", "hash"] = "fastembed"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    cache_dir: Path = Path(".cache")
    model_cache_dir: Optional[str] = None
    min_score: Optional[float] = Field(None, ge=-1.0, le=1.0)  # None -> DEFAULT_MIN_SCORE[backend]
    hybrid_bm25_enabled: bool = False  # P2-FR-03: off by default - see EVALUATION_REPORT.md for why
    # the measured improvement doesn't yet justify flipping the real default (it only covers
    # local-only retrieval; arXiv-mixed retrieval, the actual default path, is structurally
    # biased against arXiv results by this fusion and wasn't validated). Set
    # HYBRID_BM25_ENABLED=true for local-only deployments where it was actually measured.

    # LLM
    providers: tuple[ProviderSpec, ...] = ()
    llm_provider_order: tuple[str, ...] = KNOWN_PROVIDERS
    llm_timeout: float = Field(25.0, gt=0, le=120)
    llm_max_attempts: int = Field(2, ge=1, le=3)
    require_llm: bool = False
    claim_check: Literal["off", "llm"] = "off"  # P2-FR-04: LLM-judge claim support (core.claim_support)
    # Phase 3 query rewriting (core.query_rewrite); off = the measured baseline
    query_rewrite: Literal["off", "single", "multi", "decompose", "selective"] = "off"
    rewrite_providers: tuple[ProviderSpec, ...] = ()  # empty: rewrite with the main provider chain
    rewrite_gap_score: float = Field(0.35, ge=-1.0, le=1.0)  # selective: rewrite when top score is below this

    # arXiv
    arxiv_enabled: bool = True
    arxiv_max_results: int = Field(12, ge=1, le=50)
    arxiv_budget_s: float = Field(9.0, gt=0, le=60)
    arxiv_cache_ttl: int = Field(3600, ge=0)

    # API behaviour
    answer_cache_ttl: int = Field(3600, ge=0)
    rate_limit_per_min: int = Field(20, ge=0)
    trust_proxy_headers: bool = True
    allowed_origins: tuple[str, ...] = ("*",)
    site_url: str = "http://localhost:8000"
    max_pdf_mb: float = Field(4.0, gt=0, le=50)  # Vercel rejects bodies over ~4.5 MB
    max_pdf_chunks: int = Field(300, ge=1, le=5000)

    # observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    metrics_enabled: bool = True

    # workspace platform (optional)
    database_url: str = Field("", repr=False)
    storage_backend: Literal["local", "supabase"] = "local"
    storage_dir: Path = Path(".storage")
    storage_bucket: str = "documents"
    supabase_url: str = ""
    supabase_service_key: str = Field("", repr=False)
    max_upload_mb: float = Field(20.0, gt=0, le=200)
    supabase_jwt_secret: str = Field("", repr=False)
    redis_url: str = Field("", repr=False)
    workspace_rate_limit_per_min: int = Field(60, ge=0)

    def min_score_for(self, backend: str) -> float:
        """MIN_SCORE if set explicitly, else the calibrated default for the backend actually in use
        (which is "hash" when fastembed was requested but failed to load)."""
        return self.min_score if self.min_score is not None else DEFAULT_MIN_SCORE[backend]

    @property
    def max_pdf_bytes(self) -> int:
        return int(self.max_pdf_mb * 1024 * 1024)

    @model_validator(mode="after")
    def _check_combinations(self) -> Settings:
        unknown = [p for p in self.llm_provider_order if p not in KNOWN_PROVIDERS]
        if unknown:
            raise ValueError(f"unknown provider(s) {unknown}; expected any of {list(KNOWN_PROVIDERS)}")
        if self.storage_backend == "supabase" and not (self.supabase_url and self.supabase_service_key):
            raise ValueError("STORAGE_BACKEND=supabase requires SUPABASE_URL and SUPABASE_SERVICE_KEY")
        if self.require_llm and not self.providers:
            raise ValueError(
                "REQUIRE_LLM=true but no LLM provider is configured "
                "(set GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY or LLM_BASE_URL+LLM_MODEL)"
            )
        return self


def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    return v.strip() if v is not None and v.strip() != "" else None


def _csv(v: Optional[str]) -> Optional[tuple[str, ...]]:
    return None if v is None else tuple(x.strip() for x in v.split(",") if x.strip())


def _build_providers(order: tuple[str, ...], site: str) -> tuple[ProviderSpec, ...]:
    # llama-3.1-8b-instant and gemini-2.5-flash were retired in 2026 and now return 404.
    groq_model = _env("GROQ_MODEL") or "openai/gpt-oss-20b"
    catalog = {
        "groq": ProviderSpec(
            "groq",
            "https://api.groq.com/openai/v1",
            groq_model,
            _env("GROQ_API_KEY") or "",
            # gpt-oss is a reasoning model; keep reasoning short so it doesn't eat the token budget
            extra={"reasoning_effort": "low"} if groq_model.startswith("openai/gpt-oss") else {},
        ),
        "gemini": ProviderSpec(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            _env("GEMINI_MODEL") or "gemini-3.5-flash",
            _env("GEMINI_API_KEY") or "",
        ),
        "openrouter": ProviderSpec(
            "openrouter",
            "https://openrouter.ai/api/v1",
            # meta-llama/llama-3.3-70b-instruct:free was removed from OpenRouter's catalog (404,
            # confirmed live against /api/v1/models during Phase 2 B-5 verification, 2026-10-17).
            _env("OPENROUTER_MODEL") or "liquid/lfm-2.5-2.6b:free",
            _env("OPENROUTER_API_KEY") or "",
            {"HTTP-Referer": site, "X-Title": "Contexta"},
        ),
        "custom": ProviderSpec(
            "custom",
            (_env("LLM_BASE_URL") or "").rstrip("/"),
            _env("LLM_MODEL") or "",
            _env("LLM_API_KEY") or "",
        ),
    }
    out = []
    for name in order:
        spec = catalog.get(name)
        if spec is None:
            continue  # reported by Settings validation
        # custom endpoints (e.g. Ollama) may not need a key, but need URL + model
        ready = bool(spec.base_url and spec.model) if name == "custom" else bool(spec.api_key)
        if ready:
            out.append(spec)
    return tuple(out)


def load_settings() -> Settings:
    """Build Settings from the environment. Raises ConfigError on invalid configuration."""
    raw: dict[str, object] = {}
    for name in Settings.model_fields:
        if name in ("providers", "llm_provider_order", "allowed_origins", "rewrite_providers"):
            continue
        value = _env(_ENV_NAMES.get(name, name.upper()))
        if value is not None:
            raw[name] = value
    if "cache_dir" not in raw and os.getenv("VERCEL"):
        raw["cache_dir"] = "/tmp/contexta-cache"  # Vercel's filesystem is read-only except /tmp
    if "log_level" in raw:
        raw["log_level"] = str(raw["log_level"]).upper()
    if (origins := _csv(_env("ALLOWED_ORIGINS"))) is not None:
        raw["allowed_origins"] = origins
    order = _csv(_env("LLM_PROVIDERS")) or KNOWN_PROVIDERS
    raw["llm_provider_order"] = order
    raw["providers"] = _build_providers(order, str(raw.get("site_url", "http://localhost:8000")))
    if (rewrite_order := _csv(_env("REWRITE_PROVIDERS"))) is not None:
        unknown = [p for p in rewrite_order if p not in KNOWN_PROVIDERS]
        if unknown:
            raise ConfigError(f"Invalid configuration - REWRITE_PROVIDERS: unknown provider(s) {unknown}")
        raw["rewrite_providers"] = _build_providers(rewrite_order, str(raw.get("site_url", "http://localhost:8000")))
        if not raw["rewrite_providers"]:
            raise ConfigError(
                "Invalid configuration - REWRITE_PROVIDERS: none of the listed providers is configured "
                "(missing API key, or LLM_BASE_URL+LLM_MODEL for custom)"
            )
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            loc = err["loc"][0] if err["loc"] else None
            msg = err["msg"].removeprefix("Value error, ")
            # the offending value is deliberately not echoed
            problems.append(f"{_ENV_NAMES.get(loc, str(loc).upper())}: {msg}" if loc else msg)
        raise ConfigError("Invalid configuration - " + "; ".join(problems)) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
