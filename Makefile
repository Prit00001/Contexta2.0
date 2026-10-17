# Common tasks. Windows users: run the commands shown after each target directly.
PY ?= python

.PHONY: install run run-demo fake-llm test test-db lint format smoke eval lock

install:          ## create .venv and install pinned dev dependencies
	$(PY) -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

run:              ## start the API on :8000 (reads .env)
	uvicorn app.main:app --reload

fake-llm:         ## start the offline OpenAI-compatible stub LLM on :9009
	$(PY) scripts/fake_llm_server.py --port 9009

run-demo:         ## start the API wired to the stub LLM (run `make fake-llm` in another terminal)
	LLM_PROVIDERS=custom LLM_BASE_URL=http://127.0.0.1:9009/v1 LLM_MODEL=fake-llm uvicorn app.main:app

test:             ## full default suite (no network, no API keys; DB tests skip without DATABASE_URL)
	$(PY) -m pytest -q

test-db:          ## include DB integration tests (needs `docker compose up -d db`)
	DATABASE_URL=postgresql://postgres:postgres@localhost:55432/contexta $(PY) scripts/migrate.py
	DATABASE_URL=postgresql://postgres:postgres@localhost:55432/contexta $(PY) -m pytest -q

lint:             ## ruff lint + format check
	ruff check . && ruff format --check .

format:
	ruff format .

smoke:            ## end-to-end checks against a running server
	$(PY) scripts/smoke_test.py --base-url http://127.0.0.1:8000

eval:             ## retrieval-only eval metrics, offline (add --with-llm yourself for the full run)
	$(PY) eval/run_eval.py

lock:             ## re-pin dependencies after editing requirements*.in
	uv pip compile requirements.in --universal --python-version 3.11 -o requirements.txt
	uv pip compile requirements-dev.in --universal --python-version 3.11 -o requirements-dev.txt
