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
    kw.setdefault("arxiv", None)
    return ResearchRetriever(embedder=embedder, library=library, **kw)


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


class RecordingCrossEncoder:
    def __init__(self, scores):
        self.scores, self.texts = scores, []

    def rerank(self, query, documents):
        self.texts = list(documents)
        return self.scores[: len(self.texts)]


def test_rerank_library_boost_and_max_chars():
    srcs = [
        {"id": "a", "source": "arxiv", "text": "x" * 1000},
        {"id": "l", "source": "local", "text": "y" * 1000},
    ]
    enc = RecordingCrossEncoder([2.0, 0.5])  # arXiv scores higher...
    rr = Reranker("fake", model=enc, max_chars=300)
    assert [s["id"] for s in asyncio.run(rr.rerank(Q, srcs, 2))] == ["a", "l"]
    assert all(len(t) == 300 for t in enc.texts)  # only the start of each passage is scored
    boosted = asyncio.run(rr.rerank(Q, srcs, 2, library_boost=3.0))  # ...until the library prior applies
    assert [s["id"] for s in boosted] == ["l", "a"] and boosted[0]["rerank_score"] == 0.5  # reported score raw


def test_library_boost_reorders_but_never_admits(tmp_path):
    class FakeArxiv:
        async def search(self, question):
            return [{"id": "arxiv_9999.00001", "source": "arxiv", "title": "Retrieval augmented generation hallucination",
                     "url": "https://arxiv.org/abs/9999.00001", "authors": [], "published": "2026-01-01",
                     "text": "Retrieval augmented generation hallucination. How retrieval augmented generation reduces hallucination."}]  # fmt: skip

    def top(boost, min_score=0.0):
        r = _retriever(tmp_path, min_score=min_score, arxiv=FakeArxiv(), arxiv_enabled=True, library_boost=boost)
        return asyncio.run(r.aretrieve(Q, 3, use_arxiv=True)).sources

    assert top(0.0)[0]["source"] == "arxiv"
    boosted = top(0.5)
    assert boosted[0]["source"] == "local" and boosted[0]["score"] < top(0.0)[0]["score"]  # score stays raw
    strict = top(0.5, min_score=0.99)
    assert all(s["score"] >= 0.99 for s in strict)  # the prior never lets a library source past MIN_SCORE
