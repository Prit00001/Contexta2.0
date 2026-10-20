# Deployment

## Supported targets

| Target | How | Status in Phase 1 |
|---|---|---|
| Local process | `uvicorn app.main:app` | Verified |
| Docker image | `Dockerfile` (Python 3.11-slim, lockfile install, model baked in) | Defined; **build not executed** in the Phase 1 environment (Docker Hub blocked). CI builds and smoke-tests it |
| Docker Compose | `docker-compose.yml` — api, worker, one-shot migrate, Postgres/pgvector | Syntax validated (`docker compose config`); not run |
| Render (free) | `render.yaml` blueprint, Docker runtime, health check `/health` | Unchanged from baseline; not re-deployed in Phase 1 |
| Hugging Face Spaces | Docker SDK, `app_port: 8000` in the Space README | Unchanged from baseline |

## Reproducible procedure (Docker)

```bash
git checkout <release tag or commit>
docker build -t contexta:<version> .           # installs requirements.txt (pinned) + bakes the model
docker run -d --name contexta -p 8000:8000 \
  --env-file .env -e LOG_FORMAT=json contexta:<version>
python scripts/smoke_test.py --base-url http://127.0.0.1:8000          # add --expect-llm when a key is set
```

Inputs that determine the build: the git commit, `requirements.txt` (exact pins), and the base image tag `python:3.11-slim`. For byte-for-byte reproducibility pin the base image by digest (`FROM python:3.11-slim@sha256:...`) in your release branch.

### Render

Full product (workspaces, sign-up, billing): follow [GO_LIVE.md](GO_LIVE.md). Paper search only:

1. Push the repository to GitHub; Render → New → Blueprint (reads `render.yaml`).
2. Set provider keys as secret env vars in the dashboard; optionally `REQUIRE_LLM=true`, `LOG_FORMAT=json`.
3. Render's health check uses `/health`. Use `/ready` in your own monitoring to detect degraded quality.
4. Free instances sleep after ~15 min idle; an external pinger on `/health` keeps one warm.

## Health checks

| Endpoint | Use for | Semantics |
|---|---|---|
| `GET /health` | Liveness / platform health check / container `HEALTHCHECK` | 200 while the process serves requests |
| `GET /ready` | Readiness gate, monitoring, post-deploy verification | 200 `ready` or `degraded` (serving with reduced quality — reasons per check), 503 `not_ready` (index empty / not initialised) |

`/ready` makes no outbound calls (arXiv/LLM quota is not spent by probes). It reports LLM availability from configuration and current cooldown state.

Post-deploy gate (recommended): `/ready` returns 200 **and** `checks.embeddings.ok` is true **and** `scripts/smoke_test.py` passes.

## Configuration
See [CONFIGURATION.md](CONFIGURATION.md). Secrets are passed as environment variables by the platform; the image contains no `.env` (`.dockerignore`).

## Rollback

- The service is stateless apart from in-memory caches; rolling back = redeploying the previous image tag. No data migration is involved for the public API.
- Workspace platform: SQL migrations in `supabase/migrations/` are forward-only (`scripts/migrate.py` records applied files). Phase 1 added **no** migrations. If a future release adds one, write and test a reverse script before deploying, and take a DB backup first.
- Keep the previous image tag available; tag releases (`contexta:2.0.0`) rather than deploying `latest`.

## Known operational limitations

- One uvicorn worker by default (512 MB RAM target). Caches and the `/ask` rate limiter are per process; running several instances multiplies the effective rate limit and lowers cache hit rate (Phase 2 P2-11).
- With no network at startup, fastembed retries the model download for ~45 s, then falls back to hash embeddings (`/ready` → degraded). The Docker image avoids this by baking the model in.
- Free-tier LLM model IDs are retired periodically → `diagnostics` show `HTTP 404; check <VAR>`; update the variable.
- Live provider compatibility of `max_completion_tokens` (sent by `langchain-openai`) is unverified for Gemini's OpenAI-compatible endpoint.
- No authentication on `/ask` by design; put it behind a gateway if exposing beyond a demo.
