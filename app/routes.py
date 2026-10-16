from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.observability import question_fingerprint
from app.schemas import (
    ASK_RESPONSE_EXAMPLE,
    AskRequest,
    AskResponse,
    ErrorResponse,
    HealthResponse,
    ReadyCheck,
    ReadyResponse,
)
from core.pdf import chunk, extract_pages
from core.storage import safe_filename

router = APIRouter()
log = logging.getLogger("contexta.api")


class RateLimiter:
    """Sliding-window limiter per client IP. Protects your free-tier API keys from abuse."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        q = self._hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        if len(self._hits) > 5000:  # bound memory
            self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
        return True


def _client_ip(request: Request) -> str:
    """Client address for rate limiting (SR-36, defect D-06).

    Behind a platform proxy (Render / HF / Vercel) the socket peer is the proxy, so the
    client IP comes from X-Forwarded-For. Only the right-most entry is trusted: it is the
    one the platform's proxy appended. Left-most entries are whatever the client sent, so
    using them (as the baseline did) let anyone bypass the limit with a fake header.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd and request.app.state.settings.trust_proxy_headers:
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def _check_rate_limit(request: Request) -> None:
    if not request.app.state.limiter.allow(_client_ip(request)):
        log.info("rate limited", extra={"event": "ratelimit", "category": "rate_limited"})
        raise HTTPException(
            429,
            "Too many requests. Please wait a minute and try again.",
            headers={"Retry-After": "60"},
        )


def _log_result(kind: str, question: str, result: dict, **fields) -> None:
    category = {"insufficient_evidence": "insufficient_evidence", "extractive_fallback": "llm_unavailable"}.get(
        result["outcome"]
    )
    log.info(
        "%s answered: outcome=%s sources=%d", kind, result["outcome"], result["total_sources"],
        extra={"event": f"{kind}.result", "outcome": result["outcome"], "n_sources": result["total_sources"],
               "llm_used": result["llm_used"], "provider": result["provider"], "cached": result["cached"],
               "duration_ms": result.get("timings_ms", {}).get("total"), "category": category,
               **question_fingerprint(question), **fields},
    )  # fmt: skip


ERROR_RESPONSES = {
    422: {"description": "Validation error (FastAPI default body)"},
    429: {
        "model": ErrorResponse,
        "description": "Rate limit exceeded; retry after the `Retry-After` header (seconds)",
        "content": {"application/json": {"example": {"detail": "Too many requests. Please wait a minute and try again."}}},
    },
    500: {
        "model": ErrorResponse,
        "description": "Unexpected server error; quote the request_id when reporting it",
        "content": {"application/json": {"example": {"detail": "Internal server error", "request_id": "3f9c1a7e2b4d6f80"}}},
    },
    503: {"model": ErrorResponse, "description": "Retrieval temporarily unavailable"},
}


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Answer a research question from the paper library and arXiv",
    responses={200: {"content": {"application/json": {"example": ASK_RESPONSE_EXAMPLE}}}, **ERROR_RESPONSES},
)
async def ask(body: AskRequest, request: Request):
    """Runs the LangGraph research workflow: validate -> retrieve (FAISS library + optional live arXiv)
    -> generate (LLM provider chain) -> validate citations -> respond. Falls back to an extractive
    answer when no LLM succeeds, and says so when nothing relevant is found (`outcome`)."""
    _check_rate_limit(request)
    result = await request.app.state.service.ask(body.question, body.top_k, body.use_arxiv)
    _log_result("ask", body.question, result, top_k=body.top_k, use_arxiv=body.use_arxiv)
    return {**result, "request_id": _request_id(request)}


@router.post(
    "/ask-pdf",
    response_model=AskResponse,
    summary="Answer a question about an uploaded PDF",
    responses={
        400: {"description": "Empty file"}, 413: {"description": "PDF larger than MAX_PDF_MB"},
        415: {"description": "Not a PDF"}, **ERROR_RESPONSES,
    },
)  # fmt: skip
async def ask_pdf(
    request: Request,
    question: str = Form(..., min_length=3, max_length=1000),
    file: UploadFile = File(...),
    top_k: int = Form(5, ge=1, le=10),
):
    _check_rate_limit(request)

    max_bytes = request.app.state.settings.max_pdf_bytes
    data = await file.read(max_bytes + 1)  # never buffer more than the limit

    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(data) > max_bytes:
        raise HTTPException(
            413, f"PDF too large. Max size is {max_bytes // (1024 * 1024)} MB."
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "That file isn't a PDF.")

    # pypdf parsing is CPU-bound and synchronous; keep it off the event loop.
    try:
        pages = await run_in_threadpool(extract_pages, data)
    except Exception:  # noqa: BLE001 - pypdf raises many types on bad input
        raise HTTPException(
            422, "Couldn't read this PDF. It may be password-protected or corrupted."
        )

    chunks = await run_in_threadpool(chunk, pages)
    if not chunks:
        raise HTTPException(
            422,
            "No extractable text found. Scanned PDFs need OCR, which isn't supported yet.",
        )

    result = await request.app.state.service.ask_pdf(
        question=question,
        chunks=chunks,
        top_k=top_k,
        filename=safe_filename(file.filename or "uploaded.pdf"),
    )
    _log_result("ask_pdf", question, result, top_k=top_k)
    return {**result, "request_id": _request_id(request)}


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    st = request.app.state
    return HealthResponse(
        status="ok",
        index_size=st.retriever.size,
        model=st.embedder.model_name,
        embedding_backend=st.embedder.backend,
        llm_providers=st.generator.provider_names,
        arxiv_enabled=st.settings.arxiv_enabled,
    )


def _db_ping(pool) -> None:
    from core import db

    conn = db.get_pool_conn(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        db.put_pool_conn(pool, conn)


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "Not ready (index empty or not initialised)"}},
)
async def ready(request: Request):
    """Readiness: is this instance able to answer research questions, and at what quality?

    Never makes outbound calls to arXiv or LLM providers (that would spend quota on every
    probe); reports configuration and local state only. Contains no secrets.
    """
    st = request.app.state
    checks: dict[str, ReadyCheck] = {}
    service = getattr(st, "service", None)
    if service is None:
        checks["service"] = ReadyCheck(ok=False, detail="not initialised")
        body = ReadyResponse(status="not_ready", checks=checks)
        return JSONResponse(body.model_dump(), status_code=503)

    size = st.retriever.size
    checks["index"] = ReadyCheck(ok=size > 0, detail=f"{size} documents indexed")
    emb = st.embedder
    checks["embeddings"] = ReadyCheck(
        ok=not emb.degraded,
        detail=f"{emb.backend} ({emb.model_name})"
        + (" - model unavailable, using hash fallback; retrieval quality is reduced" if emb.degraded else ""),
    )
    available = st.generator.available_provider_names
    names = st.generator.provider_names
    if not names:
        checks["llm"] = ReadyCheck(ok=False, detail="no provider configured - extractive answers only")
    elif not available:
        checks["llm"] = ReadyCheck(ok=False, detail="all providers cooling down after errors")
    else:
        checks["llm"] = ReadyCheck(ok=True, detail="available: " + ", ".join(available))
    checks["arxiv"] = ReadyCheck(ok=True, detail="enabled" if st.settings.arxiv_enabled else "disabled")
    if not getattr(st, "db_configured", st.db_pool is not None):
        checks["database"] = ReadyCheck(ok=True, detail="not configured (workspace features off)")
    elif st.db_pool is None:
        checks["database"] = ReadyCheck(ok=False, detail="unreachable at startup (workspace features off)")
    else:
        try:
            await asyncio.wait_for(asyncio.to_thread(_db_ping, st.db_pool), timeout=3.0)
            checks["database"] = ReadyCheck(ok=True, detail="reachable")
        except Exception:  # noqa: BLE001 - any failure means "unreachable"; details stay in logs
            checks["database"] = ReadyCheck(ok=False, detail="unreachable")

    if not checks["index"].ok:
        status = "not_ready"
    elif all(c.ok for c in checks.values()):
        status = "ready"
    else:
        status = "degraded"
    body = ReadyResponse(status=status, checks=checks)
    return JSONResponse(body.model_dump(), status_code=503 if status == "not_ready" else 200)
