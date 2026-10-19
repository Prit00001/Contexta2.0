from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.observability import RequestContextMiddleware, configure_logging
from app.routes import router
from app.routes_admin import router as admin_router
from app.routes_workspace import router as workspace_router
from core import db, tracing
from core.cache import make_answer_cache
from core.embeddings import Embedder
from core.generator import Generator
from core.rate_limit import make_limiter, make_redis
from core.reranker import Reranker
from core.retriever import Retriever
from data.documents import DOCUMENTS
from services.arxiv_client import ArxivClient
from services.search_service import SearchService

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("contexta")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    tracing.configure_tracing(s.tracing_enabled)
    embedder = Embedder(s.embed_backend, s.embed_model, s.model_cache_dir)
    retriever = Retriever(embedder, s.cache_dir / "embeddings.npz")
    retriever.build(DOCUMENTS)
    client = httpx.AsyncClient(
        headers={"User-Agent": "Contexta/2.0 (+https://github.com/Prit00001/Contexta)"},
        follow_redirects=True,
        transport=getattr(app.state, "http_transport", None),  # injected by tests; None = real network
    )
    generator = Generator(s.providers, client, s.llm_timeout)
    arxiv = ArxivClient(client, s.arxiv_max_results, s.arxiv_cache_ttl)
    app.state.settings = s
    app.state.embedder, app.state.retriever, app.state.generator = embedder, retriever, generator
    redis = make_redis(s.redis_url)  # shared answer cache + rate limits across instances (P2-FR-07)
    app.state.redis = redis
    app.state.service = SearchService(
        s,
        embedder,
        retriever,
        arxiv,
        generator,
        answer_cache=make_answer_cache(redis, s.answer_cache_ttl),
        reranker=Reranker(s.rerank_model, s.model_cache_dir, max_chars=s.rerank_max_chars) if s.rerank_model else None,
    )
    app.state.limiter = make_limiter(redis, s.rate_limit_per_min)
    app.state.db_configured = bool(s.database_url)
    app.state.db_pool = None
    if s.database_url:
        try:
            app.state.db_pool = db.make_pool(s.database_url)
        except Exception as exc:  # noqa: BLE001 - the public research API must not depend on the DB
            log.error(
                "Database unreachable at startup (%s); workspace features disabled",
                type(exc).__name__,
                extra={"event": "startup.db", "status": "error", "category": "internal"},
            )
    app.state.workspace_limiter = make_limiter(redis, s.workspace_rate_limit_per_min)
    log.info(
        "Ready: %d docs, embeddings=%s%s, LLM providers=%s, db=%s, cache=%s",
        retriever.size,
        embedder.backend,
        " (FALLBACK)" if embedder.degraded else "",
        generator.provider_names or "none (extractive mode)",
        "connected" if app.state.db_pool else ("UNREACHABLE" if s.database_url else "not configured"),
        "redis" if redis is not None else "memory",
        extra={"event": "startup", "status": "degraded" if embedder.degraded else "ok"},
    )
    yield
    tracing.shutdown_tracing()
    await client.aclose()
    if redis is not None:
        await redis.aclose()
    if app.state.db_pool:
        app.state.db_pool.closeall()


def create_app(http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """App factory. ``http_transport`` replaces the network for every outbound call (arXiv, LLM
    providers) - integration tests pass an ``httpx.MockTransport``; production passes nothing."""
    app = FastAPI(
        title="Contexta 2.0",
        version="2.0.0",
        description=(
            "Research question answering grounded in a curated ML/NLP paper library, live arXiv search "
            "and uploaded PDFs. Answers cite retrieved sources with [n] markers; `citations`, `claims` and "
            "`citation_check` describe which markers were validated against retrieved sources."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(get_settings().allowed_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)  # outermost: sees every response, incl. CORS and 500s
    app.state.http_transport = http_transport
    app.include_router(router)
    app.include_router(admin_router)
    app.include_router(workspace_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/workspace", include_in_schema=False)
    async def workspace_page():
        return FileResponse(STATIC / "workspace.html")

    return app


app = create_app()
