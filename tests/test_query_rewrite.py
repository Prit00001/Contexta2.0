"""Phase 3 query rewriting: parsing/fusion units, and every QUERY_REWRITE mode through /ask with the
network mocked (rewriter and answerer are both httpx.MockTransport routes)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, get_settings, load_settings
from app.main import create_app
from core.query_rewrite import REWRITE_MARKER, fuse, parse_queries, retrieval_queries

QUESTION = "how to make finetuning of big language models cheaper"
ANSWER = "Low-rank adapters reduce trainable parameters [1]."


def test_parse_queries_dedupes_and_drops_junk():
    text = 'ok {"queries": ["LoRA low-rank adaptation", "lora LOW-rank adaptation", "", 7, "x", "QLoRA"]}'
    assert parse_queries(text, "q") == ["LoRA low-rank adaptation", "QLoRA"]
    assert parse_queries('{"queries": ["  The   Question "]}', "the question") is None  # only the original
    assert parse_queries("no json", "q") is None and parse_queries('{"queries": "x"}', "q") is None
    many = json.dumps({"queries": [f"query number {i}" for i in range(9)]})
    assert len(parse_queries(many, "q")) == 4


def test_retrieval_queries_keep_the_original_except_single():
    assert retrieval_queries("q", ["a", "b"], "multi") == ["q", "a", "b"]
    assert retrieval_queries("q", ["a", "b"], "single") == ["a"]
    assert retrieval_queries("q", None, "multi") == ["q"]


def test_fuse_orders_by_rank_and_keeps_best_score():
    a = [{"chunk_id": "x", "score": 0.9}, {"chunk_id": "y", "score": 0.5}]
    b = [{"chunk_id": "y", "score": 0.6}, {"chunk_id": "z", "score": 0.4}]
    out = fuse([a, b], top_k=2)
    assert [s["chunk_id"] for s in out] == ["y", "x"]  # y appears in both lists
    assert out[0]["score"] == 0.6


# ---- through /ask -----------------------------------------------------------------------------


class Net:
    def __init__(self, rewrite_reply: str | None = None):
        self.rewrite_reply = rewrite_reply
        self.calls: list[tuple[str, str]] = []  # (host, "rewrite" | "answer" | "arxiv")

    def __call__(self, req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "export.arxiv.org":
            self.calls.append((host, "arxiv"))
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        body = json.loads(req.content)
        if REWRITE_MARKER in body["messages"][0]["content"]:
            self.calls.append((host, "rewrite"))
            if self.rewrite_reply is None:
                return httpx.Response(503, json={"error": {"message": "down"}})
            text = self.rewrite_reply
        else:
            self.calls.append((host, "answer"))
            text = ANSWER
        return httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]
            },
        )

    def kinds(self) -> list[str]:
        return [k for _, k in self.calls]


@pytest.fixture
def ask(monkeypatch):
    def run(net: Net, question: str = QUESTION, use_arxiv: bool = False, **env) -> dict:
        monkeypatch.setenv("LLM_PROVIDERS", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        with TestClient(create_app(http_transport=httpx.MockTransport(net))) as c:
            r = c.post("/ask", json={"question": question, "top_k": 3, "use_arxiv": use_arxiv})
        assert r.status_code == 200, r.text
        return r.json()

    yield run
    get_settings.cache_clear()


def stages(d: dict) -> list[str]:
    return [w["stage"] for w in d["workflow"]]


def test_off_is_the_unchanged_baseline(ask):
    net = Net('{"queries": ["unused"]}')
    d = ask(net)
    assert "rewrite" not in stages(d) and d["query_rewrites"] == [] and net.kinds() == ["answer"]


@pytest.mark.parametrize("mode", ["single", "multi", "decompose"])
def test_rewrite_first_modes_make_one_rewrite_call(ask, mode):
    net = Net('{"queries": ["LoRA low-rank adaptation of large language models", "parameter-efficient fine-tuning"]}')
    d = ask(net, QUERY_REWRITE=mode)
    assert stages(d)[:3] == ["validate_request", "rewrite", "retrieve"]
    assert net.kinds() == ["rewrite", "answer"]
    expected = ["LoRA low-rank adaptation of large language models", "parameter-efficient fine-tuning"]
    assert d["query_rewrites"] == expected and d["outcome"] == "answered"


def test_rewrites_never_query_arxiv(ask):
    net = Net('{"queries": ["LoRA low-rank adaptation", "parameter-efficient fine-tuning"]}')
    ask(net, use_arxiv=True, QUERY_REWRITE="multi", ARXIV_ENABLED="true")
    assert net.kinds().count("arxiv") == 1  # the original question only, not once per rewrite


def test_failed_rewrite_falls_back_to_the_question(ask):
    net = Net(None)  # rewrite call -> HTTP 503 from the only provider
    d = ask(net, QUERY_REWRITE="multi")
    rewrite = next(w for w in d["workflow"] if w["stage"] == "rewrite")
    assert rewrite["status"] == "failed" and d["query_rewrites"] == []
    assert any("used the original question" in m for m in d["diagnostics"])
    assert d["total_sources"] > 0  # retrieval still ran with the original question


def test_selective_skips_rewrite_when_retrieval_is_strong(ask):
    net = Net('{"queries": ["unused"]}')
    d = ask(net, QUERY_REWRITE="selective", REWRITE_GAP_SCORE="-1")
    assert "rewrite" not in stages(d) and net.kinds() == ["answer"]


def test_selective_rewrites_once_when_retrieval_is_weak(ask):
    net = Net('{"queries": ["LoRA low-rank adaptation"]}')
    d = ask(net, QUERY_REWRITE="selective", REWRITE_GAP_SCORE="1")  # nothing scores >= 1: always weak
    assert stages(d)[:4] == ["validate_request", "retrieve", "rewrite", "retrieve"]
    assert stages(d).count("rewrite") == 1 and net.kinds() == ["rewrite", "answer"]


def test_rewrite_providers_route_rewriting_elsewhere(ask):
    net = Net('{"queries": ["LoRA low-rank adaptation"]}')
    d = ask(
        net, QUERY_REWRITE="single", REWRITE_PROVIDERS="custom",
        LLM_BASE_URL="http://ollama.local:11434/v1", LLM_MODEL="qwen2.5:7b",
    )  # fmt: skip
    assert net.calls[0] == ("ollama.local", "rewrite") and net.calls[1] == ("api.groq.com", "answer")
    assert d["provider"] == "groq"


def test_rewrite_providers_must_be_configured(monkeypatch):
    monkeypatch.setenv("REWRITE_PROVIDERS", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(ConfigError, match="REWRITE_PROVIDERS"):
        load_settings()
