"""P2-07: optional cross-encoder reranking. A fake model stands in for the ONNX cross-encoder
(the real one is a download); load failure is exercised through fastembed's downloader."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from core.embeddings import Embedder
from core.lc_retriever import ResearchRetriever
from core.reranker import Reranker
from core.retriever import Retriever
from data.documents import DOCUMENTS

Q = "How does retrieval augmented generation reduce hallucination?"


class FakeCrossEncoder:
    """Prefers passages that mention 'LoRA', then shorter ones - an order hybrid scoring never gives."""

    def __init__(self):
        self.calls = []

    def rerank(self, query, documents):
        documents = list(documents)
        self.calls.append((query, len(documents)))
        return [(10.0 if "LoRA" in d else 0.0) - len(d) / 1000 for d in documents]


def _retriever(tmp_path, **kw):
    embedder = Embedder("hash", "test")
    library = Retriever(embedder, tmp_path / "emb.npz")
    library.build(DOCUMENTS)
    return ResearchRetriever(embedder=embedder, library=library, arxiv=None, **kw)


def test_reranker_reorders_candidates_and_keeps_retrieval_score(tmp_path):
    fake = FakeCrossEncoder()
    plain = asyncio.run(_retriever(tmp_path, min_score=0.0).aretrieve(Q, 3, use_arxiv=False))
    r = _retriever(tmp_path, min_score=0.0, reranker=Reranker("fake", model=fake), rerank_candidates=20)
    res = asyncio.run(r.aretrieve(Q, 3, use_arxiv=False))
    assert len(fake.calls) == 1 and 3 < fake.calls[0][1] <= 20  # over-fetched candidates (deduped), one batch
    assert "LoRA" in res.sources[0]["text"] and res.sources[0]["id"] != plain.sources[0]["id"]
    assert [s["rerank_score"] for s in res.sources] == sorted((s["rerank_score"] for s in res.sources), reverse=True)
    assert all(0.0 <= s["score"] <= 1.0 for s in res.sources)  # retrieval score kept on MIN_SCORE's scale
    assert "rerank" in res.timings_ms


def test_reranker_never_admits_what_min_score_rejected(tmp_path):
    fake = FakeCrossEncoder()
    r = _retriever(tmp_path, min_score=0.3, reranker=Reranker("fake", model=fake))
    res = asyncio.run(r.aretrieve(Q, 5, use_arxiv=False))
    assert all(s["score"] >= 0.3 for s in res.sources)
    assert len(fake.calls) <= 1


def test_unloadable_model_disables_reranking_and_ready_says_so(monkeypatch, tmp_path):
    from fastembed.common.model_management import ModelManagement

    calls = []

    def offline(cls, model, cache_dir, retries=3, **kwargs):
        calls.append(retries)
        raise ValueError("offline")

    monkeypatch.setattr(ModelManagement, "download_model", classmethod(offline))
    rr = Reranker("Xenova/ms-marco-MiniLM-L-6-v2", str(tmp_path))
    assert not rr.available and rr.error == "ValueError" and calls == [1]  # one attempt, fails fast
    assert asyncio.run(rr.rerank(Q, [{"text": "a"}, {"text": "b"}, {"text": "c"}], 2)) == [
        {"text": "a"}, {"text": "b"},
    ]  # fmt: skip

    monkeypatch.setenv("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            body = c.get("/ready").json()
            assert c.post("/ask", json={"question": Q, "use_arxiv": False}).status_code == 200
    finally:
        get_settings.cache_clear()
    assert body["checks"]["reranker"]["ok"] is False and "failed to load" in body["checks"]["reranker"]["detail"]


def test_ask_reports_rerank_time(monkeypatch):
    monkeypatch.setenv("RERANK_MODEL", "none")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            c.app.state.service.research_retriever.reranker = Reranker("fake", model=FakeCrossEncoder())
            d = c.post("/ask", json={"question": Q, "use_arxiv": False}).json()
    finally:
        get_settings.cache_clear()
    assert "rerank" in d["timings_ms"] and "LoRA" in d["sources"][0]["snippet"] + d["sources"][0]["title"]
