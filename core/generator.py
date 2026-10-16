"""LLM answer generation with a provider fallback chain.

Every provider speaks the OpenAI-style /chat/completions protocol, so one code path serves
Groq, Gemini, OpenRouter, Ollama or anything else compatible. A provider that rate-limits or
fails is put on a short cooldown and the next one is tried. If all fail, we return an
extractive answer built from the retrieved passages, so the app never goes down with the LLM.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from app.config import ProviderSpec
from core.text import clip, keywords, lexical_score, split_sentences

log = logging.getLogger("contexta.generator")

SYSTEM_PROMPT = (
    "You are Contexta, a research assistant. Answer the question using ONLY the numbered sources "
    "provided (papers or excerpts from a document the user uploaded). Cite inline right after the "
    "claims they support, like [1] or [2][3]. If the sources do not contain enough information, say "
    "so plainly instead of guessing. Be concise: 120-220 words, plain prose, no headings or bullet lists."
)

_MODEL_ENV = {
    "groq": "GROQ_MODEL",
    "gemini": "GEMINI_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "custom": "LLM_MODEL",
}


def _label(s: dict) -> str:
    if s["source"] == "pdf":
        return f"uploaded PDF, page {s.get('page', '?')}"
    return "arXiv" if s["source"] == "arxiv" else "knowledge base"


def _body(s: dict) -> str:
    """Passage text without the leading title, so the fallback never quotes a title as a sentence."""
    text, title = s["text"].strip(), s.get("title", "").strip()
    if s["source"] == "pdf":
        return text
    if title and text.lower().startswith(title.lower()):
        return text[len(title) :].lstrip(" .:-\n")
    return text.split(". ", 1)[-1] if s["source"] == "local" else text


class LLMError(Exception):
    def __init__(self, message: str, cooldown: float = 30.0):
        super().__init__(message)
        self.cooldown = cooldown


@dataclass
class GenResult:
    answer: str
    provider: str
    model: str
    llm_used: bool
    warnings: list[str] = field(default_factory=list)


def build_messages(question: str, sources: list[dict]) -> list[dict]:
    blocks = [
        f"[{i}] {s['title']} ({_label(s)})\n{clip(s['text'], 700)}"
        for i, s in enumerate(sources, 1)
    ]
    user = f"Question: {question}\n\nSources:\n\n" + "\n\n".join(blocks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def extractive_answer(question: str, sources: list[dict]) -> str:
    """No-LLM fallback: the most question-relevant sentence from each top source, with citations."""
    terms = set(keywords(question, 8))
    parts = []
    for i, s in enumerate(sources[:3], 1):
        sents = [
            x for x in split_sentences(_body(s)) if len(x) >= 40
        ] or split_sentences(_body(s))
        if not sents:
            continue
        best = max(sents, key=lambda x: (lexical_score(terms, x), -len(x)))
        parts.append(f"{clip(best, 260)} [{i}]")
    return " ".join(parts) or "No relevant passages were found."


def _retry_after(resp: httpx.Response) -> float:
    try:
        return float(resp.headers.get("retry-after", "0"))
    except ValueError:
        return 0.0


class Generator:
    def __init__(
        self,
        providers: tuple[ProviderSpec, ...],
        client: httpx.AsyncClient,
        timeout: float = 25.0,
    ):
        self.providers = providers
        self.client = client
        self.timeout = timeout
        self._cooldown_until: dict[str, float] = {}

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    @property
    def available_provider_names(self) -> list[str]:
        """Configured providers that are not currently on an error cooldown."""
        now = time.monotonic()
        return [p.name for p in self.providers if self._cooldown_until.get(p.name, 0) <= now]

    async def _call(self, p: ProviderSpec, messages: list[dict]) -> str:
        headers = {"Content-Type": "application/json", **p.headers}
        if p.api_key:
            headers["Authorization"] = f"Bearer {p.api_key}"
        # 1024 leaves room for reasoning models (gpt-oss, Gemini 3.x) to think and still answer
        payload = {
            "model": p.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024,
            **p.extra,
        }
        try:
            r = await self.client.post(
                f"{p.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException:
            raise LLMError("timed out", 30)
        except httpx.HTTPError as exc:
            raise LLMError(f"network error ({type(exc).__name__})", 30)

        if r.status_code == 429:
            raise LLMError("rate limited", min(max(_retry_after(r), 20), 120))
        if r.status_code in (401, 403):
            raise LLMError("API key rejected", 600)
        if (
            r.status_code == 404
        ):  # a config problem, not a capacity one: usually a retired model ID
            env = _MODEL_ENV.get(p.name, "the model setting")
            raise LLMError(f"model '{p.model}' not found (HTTP 404); check {env}", 600)
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}", 30)
        try:
            data = r.json()
            if "error" in data:  # some gateways return 200 + error body
                raise LLMError("provider error", 30)
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("malformed response", 30)
        if not text:
            raise LLMError("empty response", 30)
        return text

    async def generate(self, question: str, sources: list[dict]) -> GenResult:
        warnings: list[str] = []
        messages = build_messages(question, sources)
        for p in self.providers:
            if self._cooldown_until.get(p.name, 0) > time.monotonic():
                warnings.append(f"{p.name} is cooling down")
                continue
            try:
                text = await self._call(p, messages)
            except LLMError as exc:
                self._cooldown_until[p.name] = time.monotonic() + exc.cooldown
                warnings.append(f"{p.name}: {exc}")
                log.warning("LLM provider %s failed: %s", p.name, exc)
                continue
            n = len(sources)  # drop hallucinated citation numbers
            text = re.sub(
                r"\[(\d+)\]",
                lambda m: m.group(0) if 1 <= int(m.group(1)) <= n else "",
                text,
            )
            return GenResult(text, p.name, p.model, True, warnings)
        return GenResult(
            extractive_answer(question, sources), "none", "extractive", False, warnings
        )
