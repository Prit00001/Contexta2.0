"""End-to-end smoke test over real sockets: a real uvicorn process serving Contexta, a stub
OpenAI-compatible LLM process (scripts/fake_llm_server.py) as the configured provider, and
scripts/smoke_test.py driving it over HTTP. No mocks inside the app, no external network.

Set CONTEXTA_SKIP_E2E=1 to skip (e.g. on machines that forbid binding local ports).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from scripts.smoke_test import run

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.getenv("CONTEXTA_SKIP_E2E") == "1", reason="CONTEXTA_SKIP_E2E=1")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"process exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError(url)


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    llm_port, api_port = _free_port(), _free_port()
    env = {
        k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k not in ("DATABASE_URL", "LLM_BASE_URL")
    }
    env.update(
        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
        EMBED_BACKEND="hash", ARXIV_ENABLED="false", MIN_SCORE="0.0", LOG_FORMAT="json",
        CACHE_DIR=str(tmp_path_factory.mktemp("cache")), REQUIRE_LLM="true",
        LLM_PROVIDERS="custom", LLM_BASE_URL=f"http://127.0.0.1:{llm_port}/v1", LLM_MODEL="fake-llm",
    )  # fmt: skip
    llm = subprocess.Popen([sys.executable, "scripts/fake_llm_server.py", "--port", str(llm_port)], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # fmt: skip
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        _wait(f"http://127.0.0.1:{llm_port}/v1/models", llm)
        _wait(f"http://127.0.0.1:{api_port}/health", api)
        yield f"http://127.0.0.1:{api_port}"
    finally:
        for p in (api, llm):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def test_full_research_query_over_http(servers):
    results = run(servers, expect_llm=True)
    failed = [(name, detail) for name, ok, detail in results if not ok]
    assert not failed, failed
    assert len(results) >= 12


def test_answer_is_grounded_in_the_library(servers):
    d = httpx.post(f"{servers}/ask", json={"question": "What does LoRA freeze during fine-tuning?", "top_k": 3,
                                           "use_arxiv": False}, timeout=30).json()  # fmt: skip
    assert d["outcome"] == "answered" and d["provider"] == "custom" and d["model_used"] == "fake-llm"
    assert d["sources"][0]["title"].startswith("LoRA")
    # The stub quotes source text verbatim, so each cited claim must literally appear in the source
    # its marker points to - an end-to-end check that markers map to the right sources.
    assert d["claims"] and all(c["status"] == "cited" for c in d["claims"])
    for claim in d["claims"]:
        for m in claim["markers"]:
            assert claim["text"].rstrip(".")[:40] in d["sources"][m - 1]["snippet"], (claim, m)
