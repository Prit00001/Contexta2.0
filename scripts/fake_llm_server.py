"""Deterministic OpenAI-compatible stub LLM for offline demos and end-to-end smoke tests.

It answers every /v1/chat/completions call by quoting the first sentence of the first two
<source> blocks it receives, with [1]/[2] citations - so the full Contexta pipeline (retrieval,
LangChain provider call, LangGraph citation validation) runs without an API key or network.

    python scripts/fake_llm_server.py --port 9009
    LLM_PROVIDERS=custom LLM_BASE_URL=http://127.0.0.1:9009/v1 LLM_MODEL=fake-llm uvicorn app.main:app

This is a test double, not a model: it never "understands" the question.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SOURCE = re.compile(r'<source id="(\d+)"[^>]*>\n(.*?)\n</source>', re.S)


def fake_answer(messages: list[dict]) -> str:
    user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    parts = []
    for sid, body in _SOURCE.findall(user)[:2]:
        first = re.split(r"(?<=[.!?])\s+", " ".join(body.split()))
        sentence = next((s for s in first[1:] + first[:1] if len(s) > 30), first[0]).rstrip(".")
        parts.append(f"{sentence} [{sid}].")
    return " ".join(parts) or "The provided sources do not contain enough information to answer."


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "fake-llm", "object": "model"}]})
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": {"message": "invalid JSON"}})
        text = fake_answer(req.get("messages", []))
        return self._send(200, {
            "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": req.get("model", "fake-llm"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })  # fmt: skip

    def log_message(self, *args):  # keep test output quiet
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9009)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"fake LLM listening on http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
