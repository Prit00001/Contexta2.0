# Deploy on Vercel

`vercel.json` deploys the whole app (paper search, React frontend, workspaces, billing, `/about` and
the crawler files) as one Vercel Function running FastAPI. Supabase and Razorpay are set up exactly
as in [GO_LIVE.md](GO_LIVE.md) §1–2; this page replaces its Render steps.

## What `vercel.json` does

| Setting | Why |
|---|---|
| `framework: fastapi` | Vercel's Python runtime finds `app` in `app/main.py` (also `[tool.vercel] entrypoint` in `pyproject.toml`) |
| `installCommand` | Installs the pinned `requirements.txt`. Without it Vercel reads `pyproject.toml`, which lists no dependencies |
| `buildCommand` | Builds the React app (`frontend/dist`, served by FastAPI) and, when `DATABASE_URL` is set, applies database migrations |
| `functions.app/main.py` | 300 s per request (the Hobby maximum: streamed answers and in-request ingestion), and leaves tests, docs and `node_modules` out of the bundle |
| `crons` | Once a day calls `/cron/maintenance`: deletes expired conversations and ingests anything left queued |

Running on Vercel (`VERCEL=1`) also changes these defaults, each overridable: caches in `/tmp`,
uploads limited to 4 MB (Vercel rejects request bodies over 4.5 MB), and `INGEST_INLINE=true`, so an
upload is chunked and embedded inside its own request and comes back **ready**: nothing runs between
requests on Vercel, so a background worker would never pick it up.

## Steps

1. **Import the repository**: [vercel.com/new](https://vercel.com/new) → import `Contexta2.0`.
   Leave Root Directory at the repository root; `vercel.json` sets the framework, install and build.
2. **Environment Variables** (Project → Settings → Environment Variables, for Production):

   | Variable | Value |
   |---|---|
   | `GROQ_API_KEY` (and/or `GEMINI_API_KEY`, `OPENROUTER_API_KEY`) | answers |
   | `APP_SITE_URL` | `https://<project>.vercel.app` or your domain, no trailing slash |
   | `DATABASE_URL` | Supabase **Transaction pooler** URI (port **6543**), not the session pooler: serverless instances open many short connections |
   | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET` | as in GO_LIVE §1 |
   | `STORAGE_BACKEND` | `supabase` (required on Vercel: the local disk is temporary and per instance) |
   | `CRON_SECRET` | a long random string; Vercel Cron sends it to `/cron/maintenance` |
   | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PLAN_ID`, `BILLING_PRICE_LABEL`, `BILLING_ENABLED` | as in GO_LIVE §2–3 |
   | `REDIS_URL` (optional) | e.g. Upstash. Without it the answer cache and rate limits are per instance |

   Set `DATABASE_URL` for **Production only**: the build applies migrations, and preview builds
   would otherwise migrate the production database too.
3. **Deploy**. In the build log look for `Installing`, `✓ built` (frontend), then `Applying
   0001_init.sql` … on the first deploy.
4. **Point Supabase and Razorpay at the Vercel address**: Supabase → Authentication → URL
   Configuration (Site URL and `…/workspace` redirect), Razorpay webhook URL
   `https://<your-domain>/billing/razorpay/webhook`.
5. **Check**: run the checklist in GO_LIVE §4, then the search-engine steps in GO_LIVE §5.

## Limits to know

- **Cold starts.** A new instance downloads the embedding and reranking models (~90 MB each) into
  `/tmp`, so the first request after a quiet period takes ~10–20 s; later requests on that instance
  are normal. They don't fit in the 500 MB bundle next to the ~410 MB of dependencies. Setting
  `RERANK_MODEL=none` halves the download at some cost in answer quality.
- **Uploads up to 4 MB** per file (Vercel's 4.5 MB body limit). Larger files need Render or Docker.
- **Large uploads take a while to return**: ingestion happens before the response (within 300 s).
- **Daily cron on Hobby.** Vercel Hobby runs crons once a day; expired conversations are already hidden
  when read, the cron only removes them.
- **Memory**: 2 GB on Hobby, enough for the models (~410 MB peak with the reranker).

## If something fails

| Symptom | Cause |
|---|---|
| Build: `No Python manifest` / app fails with `ModuleNotFoundError` | The project's Install Command was overridden in the dashboard; clear it so `vercel.json` applies |
| Build stops at `pnpm install` | `frontend/pnpm-lock.yaml` out of date with `package.json`: run `pnpm install` locally and commit the lockfile |
| Build stops at `Applying …` | `DATABASE_URL` wrong (password, or the session pooler on port 5432 instead of 6543) |
| `/` shows the old plain page instead of the React app | The frontend build didn't run: check the Build Command is not overridden in the dashboard |
| Upload returns 413 | File over 4 MB |
| Upload fails with a storage error | `STORAGE_BACKEND=supabase` not set, or the `documents` bucket is missing |
| `/cron/maintenance` returns 404 in the cron logs | `CRON_SECRET` not set |
