"""LLM answer generation with a provider fallback chain.

Each provider is called through the LangChain adapter in ``core.llm`` (``ChatOpenAI`` over the
OpenAI-compatible protocol) with the prompt from ``core.prompts``. A provider that rate-limits
or fails is put on a cooldown and the next one is tried. If all fail, an extractive answer built
from the retrieved passages is returned, so the app never goes down with the LLM.

The chain itself stays here rather than in LangChain's ``with_fallbacks()`` because the
cooldowns must persist across requests: a rate-limited free-tier provider is skipped for the
next 20-120 s instead of being retried by every request (ADR-001).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from app.config import ProviderSpec
from core import metrics
from core.citations import strip_invalid_markers
from core.llm import LLMError, invoke_chat, make_chat_model
from core.prompts import build_prompt_messages
from core.text import clip, keywords, lexical_score, split_sentences

log = logging.getLogger("contexta.generator")

__all__ = ["GenResult", "Generator", "LLMError", "extractive_answer"]


def _body(s: dict) -> str:
    """Passage text without the leading title, so the fallback never quotes a title as a sentence."""
    text, title = s["text"].strip(), s.get("title", "").strip()
    if s["source"] == "pdf":
        return text
    if title and text.lower().startswith(title.lower()):
        return text[len(title) :].lstrip(" .:-\n")
    return text.split(". ", 1)[-1] if s["source"] == "local" else text


@dataclass
class GenResult:
    answer: str
    provider: str
    model: str
    llm_used: bool
    warnings: list[str] = field(default_factory=list)
    removed_markers: list[int] = field(default_factory=list)  # hallucinated [n] numbers that were stripped


def extractive_answer(question: str, sources: list[dict]) -> str:
    """No-LLM fallback: the most question-relevant sentence from each top source, with citations."""
    terms = set(keywords(question, 8))
    parts = []
    for i, s in enumerate(sources[:3], 1):
        sents = [x for x in split_sentences(_body(s)) if len(x) >= 40] or split_sentences(_body(s))
        if not sents:
            continue
        best = max(sents, key=lambda x: (lexical_score(terms, x), -len(x)))
        parts.append(f"{clip(best, 260)} [{i}]")
    return " ".join(parts) or "No relevant passages were found."


class Generator:
    def __init__(self, providers: tuple[ProviderSpec, ...], client: httpx.AsyncClient, timeout: float = 25.0):
        self.providers = providers
        self.client = client
        self.timeout = timeout
        self._cooldown_until: dict[str, float] = {}
        self._models = {p.name: make_chat_model(p, client, timeout) for p in providers}

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    @property
    def available_provider_names(self) -> list[str]:
        """Configured providers that are not currently on an error cooldown."""
        now = time.monotonic()
        return [p.name for p in self.providers if self._cooldown_until.get(p.name, 0) <= now]

    async def _first_answer(self, messages: list) -> tuple[str | None, ProviderSpec | None, list[str]]:
        """Try providers in order (skipping ones on cooldown); the first non-empty answer wins."""
        warnings: list[str] = []
        for p in self.providers:
            if self._cooldown_until.get(p.name, 0) > time.monotonic():
                warnings.append(f"{p.name} is cooling down")
                continue
            t0 = time.perf_counter()
            try:
                text = await invoke_chat(self._models[p.name], p, messages)
            except LLMError as exc:
                self._cooldown_until[p.name] = time.monotonic() + exc.cooldown
                metrics.LLM_CALLS.labels(p.name, "error").inc()
                warnings.append(f"{p.name}: {exc}")
                log.warning(
                    "LLM provider %s failed: %s",
                    p.name,
                    exc,
                    extra={
                        "event": "llm.call",
                        "provider": p.name,
                        "status": "error",
                        "category": "llm_unavailable",
                        "duration_ms": round((time.perf_counter() - t0) * 1000),
                        "cooldown_s": exc.cooldown,
                    },
                )
                continue
            metrics.LLM_CALLS.labels(p.name, "ok").inc()
            log.info(
                "LLM provider %s answered",
                p.name,
                extra={
                    "event": "llm.call",
                    "provider": p.name,
                    "status": "ok",
                    "duration_ms": round((time.perf_counter() - t0) * 1000),
                },
            )
            return text, p, warnings
        return None, None, warnings

    async def generate(self, question: str, sources: list[dict], feedback: str | None = None) -> GenResult:
        text, p, warnings = await self._first_answer(build_prompt_messages(question, sources, feedback))
        if p is None:
            return GenResult(extractive_answer(question, sources), "none", "extractive", False, warnings)
        text, removed = strip_invalid_markers(text, len(sources))  # drop hallucinated citation numbers
        return GenResult(text, p.name, p.model, True, warnings, removed)

    async def complete(self, messages: list) -> tuple[str | None, list[str]]:
        """Free-form call through the same chain and cooldowns (claim check, query rewriting)."""
        text, _, warnings = await self._first_answer(messages)
        return text, warnings
