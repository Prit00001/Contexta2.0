"""Live check of every research-graph node through the real HTTP API (real LLM, live arXiv).

Starts the app in-process once per configuration, sends requests chosen to drive each node, and
prints the workflow trace (stage:status) each one took. Routes a live model can't be made to take on
demand (retry, unusable, retrieval error) are covered offline by tests/test_graph_routes.py.

    GROQ_API_KEY=... PYTHONPATH=. python scripts/live_node_check.py
    PYTHONPATH=. python scripts/live_node_check.py --no-arxiv     # library only

Makes about 20 LLM calls. Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

ON_TOPIC = "How does LoRA reduce the number of trainable parameters?"
OFF_TOPIC = "What is the best recipe for sourdough bread?"
HISTORY = [{"question": "What is QLoRA?", "answer": "QLoRA fine-tunes 4-bit quantized models with LoRA adapters [1]."}]


PACE_S = 4.0  # between LLM requests: the free Groq tier allows 8,000 tokens per minute


@contextmanager
def app_with(**env: str):
    env = {"RATE_LIMIT_PER_MIN": "1000", **env}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


def trace(body: dict) -> list[str]:
    return [f"{s['stage']}:{s['status']}" for s in body.get("workflow", [])]


def stream(client: TestClient, payload: dict) -> tuple[int, list[str], dict]:
    events, final = [], {}
    with client.stream("POST", "/ask/stream", json=payload) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
                events.append(event)
            elif line.startswith("data: ") and event == "done":
                final = json.loads(line[6:])
    return r.status_code, events, final


def main(use_arxiv: bool) -> int:
    results: list[tuple[str, bool, str]] = []
    last: dict = {}

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        if not ok and last.get("diagnostics"):
            detail += f"\n      diagnostics: {last['diagnostics']}"
        print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {detail}", flush=True)

    def ask(client, question=ON_TOPIC, **extra) -> tuple[int, dict, float]:
        time.sleep(PACE_S)
        t0 = time.perf_counter()
        r = client.post("/ask", json={"question": question, "use_arxiv": use_arxiv, **extra})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        last.clear()
        last.update(body if isinstance(body, dict) else {})
        return r.status_code, body, time.perf_counter() - t0

    with app_with(QUERY_REWRITE="off", CLAIM_CHECK="off") as c:
        code, b, _ = ask(c, question="hi")
        check("validate_request rejects a bad question", code == 422, f"HTTP {code}")
        code, b, s = ask(c)
        t = trace(b)
        check(
            "retrieve -> generate -> validate_answer -> finalize",
            code == 200 and "generate:ok" in t and t[-1] == "finalize:answered" and b.get("citations"),
            f"{t}  {len(b.get('citations', []))} citations, {s:.1f}s, provider={b.get('provider')}",
        )
        code, b, _ = ask(c, question=OFF_TOPIC, use_arxiv=False)
        t = trace(b)
        check(
            "nothing relevant -> insufficient_evidence",
            b.get("outcome") == "insufficient_evidence" and ("retrieve:empty" in t or "validate_answer:abstained" in t),
            f"{t}",
        )
        code, b, _ = ask(c, question="What about its memory use?", history=HISTORY)
        t = trace(b)
        check(
            "contextualize turns a follow-up into a standalone question",
            "contextualize:ok" in t and "generate:ok" in t and b.get("outcome") == "answered",
            f"{t}  standalone={b.get('standalone_question')!r}",
        )
        time.sleep(PACE_S)
        code, events, final = stream(c, {"question": "What problem does FlashAttention solve?", "use_arxiv": use_arxiv})
        last.clear()
        last.update(final)
        check(
            "/ask/stream emits stage, sources, token and done events",
            code == 200 and {"stage", "sources", "token", "done"} <= set(events) and final.get("outcome") == "answered",
            f"HTTP {code}, {events.count('token')} tokens, {events.count('stage')} stages, "
            f"outcome={final.get('outcome')}",
        )

    with app_with(QUERY_REWRITE="off", CLAIM_CHECK="llm") as c:
        code, b, _ = ask(c)
        t = trace(b)
        cc = b.get("citation_check", {})
        check(
            "check_claims labels each cited claim",
            "generate:ok" in t and "check_claims:ok" in t and cc.get("verification") == "claim_support",
            f"{t}  supported={cc.get('supported_claims')} not_supported={cc.get('not_supported_claims')} "
            f"unknown={cc.get('unknown_claims')}",
        )

    for mode in ("single", "multi", "decompose"):
        with app_with(QUERY_REWRITE=mode, CLAIM_CHECK="off") as c:
            code, b, _ = ask(c, question="Why does freezing the base model and adding small adapters save memory?")
            t = trace(b)
            check(
                f"rewrite ({mode}) runs before retrieve",
                t[:3] == ["validate_request:ok", "rewrite:ok", "retrieve:ok"]
                and "generate:ok" in t
                and b.get("query_rewrites"),
                f"{t}  rewrites={b.get('query_rewrites')}",
            )

    with app_with(QUERY_REWRITE="selective", CLAIM_CHECK="off", REWRITE_GAP_SCORE="0.99") as c:
        code, b, _ = ask(c)
        t = trace(b)
        check(
            "selective: weak retrieval -> rewrite -> retrieve again",
            t[1:4] == ["retrieve:ok", "rewrite:ok", "retrieve:ok"] and "generate:ok" in t,
            f"{t}",
        )

    with app_with(QUERY_REWRITE="off", CLAIM_CHECK="llm", GROQ_API_KEY="", REQUIRE_LLM="false") as c:
        code, b, _ = ask(c)
        t = trace(b)
        check(
            "no provider: generate -> fallback (extractive), claims checked locally",
            "generate:no_llm" in t and "fallback:ok" in t and b.get("outcome") == "extractive_fallback",
            f"{t}",
        )

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-arxiv", action="store_true")
    sys.exit(main(use_arxiv=not ap.parse_args().no_arxiv))
