"""P2-13: POST /ask/stream - server-sent events over the same workflow, cache and response as /ask.
The LLM is a MockTransport that answers ``stream: true`` requests with OpenAI-style SSE chunks."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from core.query_rewrite import REWRITE_MARKER

QUESTION = "How does LoRA reduce trainable parameters?"


def _sse_body(text: str) -> str:
    out = []
    for i, word in enumerate(text.split(" ")):
        piece = word if i == 0 else " " + word
        delta = {"role": "assistant", "content": piece} if i == 0 else {"content": piece}
        out.append({"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})  # fmt: skip
    out.append({"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})  # fmt: skip
    return "".join(f"data: {json.dumps(c)}\n\n" for c in out) + "data: [DONE]\n\n"


class Net:
    """Answers: a scripted list of answer drafts (last repeats); rewrite calls get fixed JSON."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.streamed: list[bool] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if REWRITE_MARKER in body["messages"][0]["content"]:
            text = '{"queries": ["low-rank adaptation"]}'
        else:
            text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        self.streamed.append(bool(body.get("stream")))
        if body.get("stream"):
            return httpx.Response(200, text=_sse_body(text), headers={"content-type": "text/event-stream"})
        msg = {"role": "assistant", "content": text}
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})


def parse(text: str) -> list[tuple[str, object]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def client(monkeypatch):
    def make(net: Net, **env) -> TestClient:
        monkeypatch.setenv("LLM_PROVIDERS", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        return TestClient(create_app(http_transport=httpx.MockTransport(net)))

    yield make
    get_settings.cache_clear()


def test_stream_emits_sources_tokens_then_the_ask_response(client):
    net = Net("LoRA trains small low-rank matrices instead of all weights [1].")
    with client(net) as c:
        r = c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False, "top_k": 3})
        plain = c.post("/ask", json={"question": QUESTION + " please", "use_arxiv": False, "top_k": 3}).json()
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse(r.text)
    kinds = [k for k, _ in events]
    assert kinds.index("sources") < kinds.index("token") < kinds.index("done") == len(kinds) - 1
    tokens = "".join(p for k, p in events if k == "token")
    assert tokens == "LoRA trains small low-rank matrices instead of all weights [1]."
    done = events[-1][1]
    assert done["answer"] == tokens and done["outcome"] == "answered" and done["request_id"]
    assert set(done) == set(plain)  # same response shape as /ask
    assert net.streamed[0] is True and net.streamed[-1] is False  # /ask itself stays non-streaming
    assert [s["marker"] for s in dict(events)["sources"]] == [1, 2, 3]


def test_stream_resets_when_the_draft_fails_validation(client):
    net = Net("No citations at all in this draft.", "Grounded answer [1].")
    with client(net) as c:
        events = parse(c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).text)
    kinds = [k for k, _ in events]
    assert "reset" in kinds and dict(events)["reset"] == {"attempt": 2}
    after = "".join(p for k, p in events[kinds.index("reset") :] if k == "token")
    assert after == "Grounded answer [1]." and events[-1][1]["answer"] == "Grounded answer [1]."


def test_rewriter_tokens_are_not_streamed_to_the_client(client):
    net = Net("Answer text [1].")
    with client(net, QUERY_REWRITE="multi") as c:
        events = parse(c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).text)
    tokens = "".join(p for k, p in events if k == "token")
    assert tokens == "Answer text [1]." and "low-rank adaptation" not in tokens
    assert events[-1][1]["query_rewrites"] == ["low-rank adaptation"]


def test_cache_hit_is_a_single_done_event(client):
    net = Net("Answer text [1].")
    with client(net) as c:
        c.post("/ask", json={"question": QUESTION, "use_arxiv": False})
        events = parse(c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).text)
    assert [k for k, _ in events] == ["done"] and events[0][1]["cached"] is True


def test_no_provider_streams_the_extractive_answer_without_tokens(monkeypatch):
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        events = parse(c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).text)
    assert "token" not in [k for k, _ in events]
    assert events[-1][0] == "done" and events[-1][1]["outcome"] == "extractive_fallback"


def test_stream_validation_and_rate_limit_happen_before_streaming(client):
    with client(Net("x [1].")) as c:
        assert c.post("/ask/stream", json={"question": "hi"}).status_code == 422
        c.app.state.limiter.per_minute = 1
        assert c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).status_code == 200
        assert c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).status_code == 429


def test_retrieval_failure_is_an_error_event(client, monkeypatch):
    with client(Net("x [1].")) as c:

        async def boom(*args, **kwargs):
            raise RuntimeError("index gone")

        monkeypatch.setattr(c.app.state.service.research_retriever.__class__, "aretrieve", boom)
        events = parse(c.post("/ask/stream", json={"question": QUESTION, "use_arxiv": False}).text)
    assert events[-1][0] == "error" and events[-1][1]["status"] == 503 and events[-1][1]["request_id"]
