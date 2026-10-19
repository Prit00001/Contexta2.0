"""End-to-end smoke test against a *running* Contexta server.

    python scripts/smoke_test.py --base-url http://127.0.0.1:8000            # any configuration
    python scripts/smoke_test.py --base-url http://127.0.0.1:8000 --expect-llm   # an LLM must answer

Checks liveness, readiness, OpenAPI, validation, a full research query (answer, sources,
validated citations, request ID) and caching. Exits non-zero on any failure. Uses the bundled
paper library (no arXiv) so results are reproducible.
"""

from __future__ import annotations

import argparse
import sys

import httpx

QUESTION = "How does retrieval-augmented generation reduce hallucination?"


def run(base_url: str, expect_llm: bool = False, timeout: float = 60.0) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, bool(ok), detail))
        return bool(ok)

    with httpx.Client(base_url=base_url, timeout=timeout) as c:
        h = c.get("/health")
        check("GET /health -> 200 ok", h.status_code == 200 and h.json().get("status") == "ok",
              f"index_size={h.json().get('index_size') if h.status_code == 200 else h.status_code}")  # fmt: skip

        r = c.get("/ready")
        status = r.json().get("status") if r.headers.get("content-type", "").startswith("application/json") else None
        check("GET /ready -> ready|degraded", r.status_code == 200 and status in ("ready", "degraded"),
              f"{status}: " + ", ".join(f"{k}={'ok' if v['ok'] else v['detail']}" for k, v in r.json().get("checks", {}).items()))  # fmt: skip

        spec = c.get("/openapi.json")
        check("GET /openapi.json has /ask", spec.status_code == 200 and "/ask" in spec.json().get("paths", {}))

        bad = c.post("/ask", json={"question": "hi"})
        check("POST /ask invalid -> 422", bad.status_code == 422)

        body = {"question": QUESTION, "top_k": 3, "use_arxiv": False}
        a = c.post("/ask", json=body)
        if not check("POST /ask -> 200", a.status_code == 200, a.text[:200] if a.status_code != 200 else ""):
            return results
        d = a.json()
        allowed = ("answered",) if expect_llm else ("answered", "extractive_fallback")
        check("outcome", d["outcome"] in allowed, f"{d['outcome']} via {d['provider']}/{d['model_used']}")
        check("answer non-empty", bool(d["answer"].strip()), d["answer"][:120])
        check("sources returned with IDs", d["sources"] and all(s["document_id"] and s["chunk_id"] for s in d["sources"]),
              f"{len(d['sources'])} sources; top: {d['sources'][0]['title'] if d['sources'] else '-'}")  # fmt: skip
        check("source URLs are http(s) or empty", all(s["url"] == "" or s["url"].startswith(("http://", "https://"))
                                                       for s in d["sources"]))  # fmt: skip
        markers = {s["marker"] for s in d["sources"]}
        check("citations reference returned sources", d["citations"] and all(ci["marker"] in markers for ci in d["citations"]),
              f"citation_check={d['citation_check']}")  # fmt: skip
        check("X-Request-ID matches body", a.headers.get("x-request-id") == d["request_id"] != "", d["request_id"])
        check("workflow trace present", [w["stage"] for w in d["workflow"]][:2] == ["validate_request", "retrieve"],
              " -> ".join(w["stage"] for w in d["workflow"]))  # fmt: skip
        if d["llm_used"]:
            again = c.post("/ask", json=body).json()
            check("repeat question served from cache", again["cached"] is True)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Contexta end-to-end smoke test")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--expect-llm", action="store_true", help="fail unless an LLM (not the fallback) answered")
    args = ap.parse_args()
    try:
        results = run(args.base_url, args.expect_llm)
    except httpx.HTTPError as exc:
        print(f"FAIL  cannot reach {args.base_url}: {type(exc).__name__}")
        return 2
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
