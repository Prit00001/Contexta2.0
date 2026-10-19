# Contexta 2.0 API image. Python 3.11 matches the oldest interpreter the lockfile and test suite
# are verified on (3.11 and 3.13).

# ---- React frontend (frontend/): built here, served by FastAPI from /app/frontend/dist ----
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/pnpm-lock.yaml ./
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/app/.model_cache \
    CACHE_DIR=/app/.cache \
    ORT_DISABLE_TELEMETRY=1 \
    LOG_FORMAT=json

WORKDIR /app

# tesseract-ocr: OCR fallback for scanned PDF pages (core/parsers.py)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Pinned lockfile -> reproducible image.
COPY requirements.txt requirements-otel.txt ./
RUN pip install -r requirements.txt
# Optional OpenTelemetry export (TRACING_ENABLED=true): docker build --build-arg WITH_OTEL=true .
ARG WITH_OTEL=false
RUN if [ "$WITH_OTEL" = "true" ]; then pip install -r requirements-otel.txt; fi

# Bake the embedding model and the reranker (RERANK_MODEL default) into the image so cold starts don't download it (and so a missing
# network at runtime can't silently drop retrieval to hash embeddings).
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/.model_cache')" \
    && python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder(model_name='Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/app/.model_cache')"

COPY . .
COPY --from=frontend /fe/dist /app/frontend/dist
RUN useradd -m appuser && mkdir -p /app/.cache /app/.storage && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

# One worker keeps RAM under Render's 512 MB free tier.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
