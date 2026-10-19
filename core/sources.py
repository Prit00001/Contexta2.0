"""Source metadata normalisation and LangChain ``Document`` mapping (FR-04, FR-06, SR-35).

Internally a *source* is a plain dict (the shape the generator and extractive fallback already
use). Every source leaving retrieval passes through :func:`normalize_source`, which guarantees:

* ``document_id`` - the paper / file the passage belongs to (stable across chunks), and
  ``chunk_id`` - the specific passage (``<document_id>#<chunk index>``);
* ``url`` is ``http``/``https`` or empty - never ``javascript:``, ``file:`` or other schemes;
* ``title`` is non-empty single-line text; ``authors`` is a short list of strings; ``score``
  is finite. Malformed metadata is repaired or dropped, never passed through.

Metadata comes only from the retrieval layer (library entries, arXiv feed, uploaded file); the
LLM never supplies titles, URLs or authors.
"""

from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from langchain_core.documents import Document

MAX_URL = 2048
MAX_AUTHORS = 10
_ARXIV_ABS = re.compile(r"arxiv\.org/abs/([\w.\-/]+?)(v\d+)?/?$", re.IGNORECASE)


def safe_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if not url or len(url) > MAX_URL or any(c.isspace() for c in url):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme in ("http", "https") and parts.netloc else ""


def _clean_text(v: object, limit: int = 500) -> str:
    return " ".join(str(v).split())[:limit] if v is not None else ""


def _document_id(s: dict) -> str:
    if s.get("document_id"):
        return str(s["document_id"])
    if s.get("source") == "arxiv":
        m = _ARXIV_ABS.search(str(s.get("url", "")))
        if m:
            return f"arxiv:{m.group(1)}"
        raw = str(s.get("id") or "").removeprefix("arxiv_")
        return f"arxiv:{raw}" if raw else "unknown"
    return str(s.get("id") or "") or "unknown"


def normalize_source(s: dict, chunk_index: int = 0) -> dict:
    """Return a copy of ``s`` with validated metadata and document/chunk identifiers."""
    out = dict(s)
    out["source"] = s.get("source") if s.get("source") in ("local", "arxiv", "pdf") else "local"
    out["id"] = str(s.get("id") or "")
    out["document_id"] = _document_id(s)
    out["chunk_id"] = str(s.get("chunk_id") or f"{out['document_id']}#{chunk_index}")
    out["title"] = _clean_text(s.get("title"), 300) or "Untitled source"
    out["url"] = safe_url(s.get("url"))
    authors = s.get("authors")
    out["authors"] = (
        [_clean_text(a, 120) for a in authors if isinstance(a, str) and a.strip()][:MAX_AUTHORS]
        if isinstance(authors, list)
        else []
    )
    out["published"] = _clean_text(s.get("published"), 10)
    out["text"] = str(s.get("text") or "")
    try:
        score = float(s.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    out["score"] = score if math.isfinite(score) else 0.0
    page = s.get("page")
    out["page"] = page if isinstance(page, int) and page > 0 else None
    return out


def to_document(s: dict) -> Document:
    meta = {k: v for k, v in s.items() if k != "text"}
    return Document(page_content=s.get("text", ""), metadata=meta, id=s.get("chunk_id") or None)


def from_document(doc: Document) -> dict:
    return normalize_source({**doc.metadata, "text": doc.page_content})
