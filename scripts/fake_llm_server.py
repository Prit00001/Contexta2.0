"""Deterministic OpenAI-compatible stub LLM for offline demos and end-to-end smoke tests.

It answers every /v1/chat/completions call by quoting the first sentence of the first two
<source> blocks it receives, with [1]/[2] citations - so the full Contexta pipeline (retrieval,
LangChain provider call, LangGraph citation validation) runs without an API key or network.

    python scripts/fake_llm_server.py --port 9009
    LLM_PROVIDERS=custom LLM_BASE_URL=http://127.0.0.1:9009/v1 LLM_MODEL=fake-llm uvicorn app.main:app

Query-rewrite calls (QUERY_REWRITE, core.query_rewrite) get deterministic JSON: the question's
keywords as one query, and for decomposition one query per side of an "and"/"vs" comparison.

This is a test double, not a model: it never "understands" the question.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SOURCE = re.compile(r'<source id="(\d+)"[^>]*>\n(.*?)\n</source>', re.S)


_STOP = set(
    "a an and are as at be between by compare does do for from how in is it of on or the to vs versus what when "
    "which why with".split()
)


def _keywords(text: str) -> str:
    return " ".join(w for w in re.findall(r"[A-Za-z0-9-]+", text) if w.lower() not in _STOP)


def fake_rewrite(system: str, user: str) -> str:
    question = user.removeprefix("Question:").strip()
    queries = [_keywords(question)]
    if "self-contained query per part" in system:
        parts = re.split(r"\b(?:and|vs\.?|versus)\b", question, flags=re.I)
        if len(parts) > 1:
            queries = [_keywords(p) for p in parts if _keywords(p)]
    elif "three different queries" in system:
        queries += [_keywords(question) + " method", _keywords(question) + " paper"]
    return json.dumps({"queries": queries})


def fake_answer(messages: list[dict]) -> str:
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    if "search queries for a library of machine learning" in system:
        return fake_rewrite(system, user)
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
        if req.get("stream"):
            return self._stream(text, req.get("model", "fake-llm"))
        return self._send(200, {
            "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": req.get("model", "fake-llm"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })  # fmt: skip

    def _stream(self, text: str, model: str) -> None:
        """OpenAI-style SSE, one word per chunk with a short pause, so streaming is visible."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        base = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": model}
        for i, word in enumerate(re.findall(r"\S+\s*", text)):
            delta = {"role": "assistant", "content": word} if i == 0 else {"content": word}
            chunk = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(self.server.delay)
        done = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode())
        self.wfile.flush()

    def log_message(self, *args):  # keep test output quiet
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9009)
    ap.add_argument("--delay", type=float, default=0.03, help="seconds between streamed words")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.delay = args.delay
    print(f"fake LLM listening on http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
