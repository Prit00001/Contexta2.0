"""T-10: retriever adapters over FAISS + arXiv, including every arXiv failure mode (FR-04, FR-07)."""

from __future__ import annotations

import asyncio

import httpx
import numpy as np
import pytest
from langchain_core.documents import Document

from core.embeddings import Embedder
from core.lc_retriever import ARXIV_UNAVAILABLE, PdfChunkRetriever, ResearchRetriever, _unit
from core.retriever import Retriever
from data.documents import DOCUMENTS
from services.arxiv_client import ArxivClient

ARXIV_XML = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2005.11401v4</id><published>2020-05-22T00:00:00Z</published>
<title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
<summary>Retrieval augmented generation combines parametric memory with a dense retrieval index.</summary>
<author><name>P. Lewis</name></author></entry>
<entry><id>http://arxiv.org/abs/2401.00001v1</id><published>2024-01-01T00:00:00Z</published>
<title>A New Retrieval Augmented Generation Benchmark</title>
<summary>We benchmark retrieval augmented generation systems on hallucination.</summary>
<author><name>A. Author</name></author></entry></feed>"""


@pytest.fixture(scope="module")
def embedder():
    return Embedder("hash", "test")


@pytest.fixture(scope="module")
def library(embedder, tmp_path_factory):
    r = Retriever(embedder, tmp_path_factory.mktemp("cache") / "emb.npz")
    r.build(DOCUMENTS)
    return r


def arxiv_with(handler) -> ArxivClient:
    return ArxivClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), min_interval=0)


def make(embedder, library, arxiv, **kw) -> ResearchRetriever:
    return ResearchRetriever(embedder=embedder, library=library, arxiv=arxiv, min_score=0.0, **kw)


Q = "How does retrieval augmented generation reduce hallucination?"


def test_library_only_results_are_normalised(embedder, library):
    r = make(embedder, library, arxiv=None)
    res = asyncio.run(r.aretrieve(Q, 3, use_arxiv=False))
    assert len(res.sources) == 3 and res.warnings == []
    assert res.sources[0]["id"] == "doc_004"  # the RAG paper
    for s in res.sources:
        assert s["document_id"] and s["chunk_id"].startswith(s["document_id"] + "#") and s["url"].startswith("https://")
    assert res.sources == sorted(res.sources, key=lambda s: s["score"], reverse=True)
    assert set(res.timings_ms) == {"local", "arxiv"}


def test_arxiv_results_merged_and_deduplicated(embedder, library):
    r = make(embedder, library, arxiv_with(lambda req: httpx.Response(200, text=ARXIV_XML)))
    res = asyncio.run(r.aretrieve(Q, 10, use_arxiv=True))
    ids = [s["document_id"] for s in res.sources]
    # 2005.11401 is both a library entry (doc_004) and an arXiv hit: kept once.
    assert sum(1 for s in res.sources if "2005.11401" in s["url"]) == 1
    assert "arxiv:2401.00001" in ids and res.warnings == []


@pytest.mark.parametrize(
    "handler",
    [
        lambda req: httpx.Response(503),
        lambda req: httpx.Response(200, text="<feed><entry>not closed"),
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")),
        lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
    ],
    ids=["http-503", "malformed-xml", "connect-error", "timeout"],
)
def test_arxiv_failures_degrade_to_library_with_warning(embedder, library, handler):
    r = make(embedder, library, arxiv_with(handler))
    res = asyncio.run(r.aretrieve(Q, 3, use_arxiv=True))
    assert res.warnings == [ARXIV_UNAVAILABLE]
    assert len(res.sources) == 3 and all(s["source"] == "local" for s in res.sources)


def test_arxiv_budget_exceeded(embedder, library):
    class Slow:
        async def search(self, q):
            await asyncio.sleep(1)
            return []

    r = make(embedder, library, Slow(), arxiv_budget_s=0.05)
    res = asyncio.run(r.aretrieve(Q, 3, use_arxiv=True))
    assert res.warnings == [ARXIV_UNAVAILABLE] and res.sources


def test_arxiv_empty_feed_is_not_a_failure(embedder, library):
    empty = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    r = make(embedder, library, arxiv_with(lambda req: httpx.Response(200, text=empty)))
    res = asyncio.run(r.aretrieve(Q, 3, use_arxiv=True))
    assert res.warnings == [] and len(res.sources) == 3


def test_arxiv_disabled_by_config_is_never_called(embedder, library):
    def boom(req):
        raise AssertionError("arXiv must not be called")

    r = make(embedder, library, arxiv_with(boom), arxiv_enabled=False)
    assert asyncio.run(r.aretrieve(Q, 3, use_arxiv=True)).warnings == []


def test_min_score_cut_can_leave_no_sources(embedder, library):
    r = ResearchRetriever(embedder=embedder, library=library, arxiv=None, min_score=0.99)
    assert asyncio.run(r.aretrieve("zebra migration patterns in Kenya", 5, use_arxiv=False)).sources == []


def test_langchain_ainvoke_matches_aretrieve(embedder, library):
    r = make(embedder, library, arxiv=None)
    docs = asyncio.run(r.ainvoke(Q, top_k=4, use_arxiv=False))
    res = asyncio.run(r.aretrieve(Q, 4, use_arxiv=False))
    assert all(isinstance(d, Document) for d in docs)
    assert [d.id for d in docs] == [s["chunk_id"] for s in res.sources]
    with pytest.raises(NotImplementedError):
        r.invoke(Q)


def test_pdf_chunk_retriever(embedder):
    chunks = [
        {"page": 1, "text": "This paper studies protein folding with diffusion models."},
        {"page": 2, "text": "Our retrieval augmented generation pipeline reduces hallucination in answers."},
        {"page": 3, "text": "We thank the reviewers for their helpful comments."},
    ]
    vecs = _unit(np.asarray(embedder.embed_sync([c["text"] for c in chunks]), dtype=np.float32))
    r = PdfChunkRetriever(
        embedder=embedder, chunks=chunks, chunk_vectors=vecs, filename="paper.pdf", document_id="pdf:x"
    )
    res = asyncio.run(r.aretrieve(Q, 2))
    top = res.sources[0]
    assert top["page"] == 2 and top["chunk_id"] == "pdf:x#1" and top["document_id"] == "pdf:x"
    assert top["title"] == "paper.pdf · p. 2" and top["url"] == "" and len(res.sources) == 2


def test_arxiv_gets_its_own_cutoff(embedder, library):
    """MIN_SCORE_ARXIV: arXiv results face their own cutoff, library results keep MIN_SCORE."""
    ok = lambda req: httpx.Response(200, text=ARXIV_XML)  # noqa: E731
    both = asyncio.run(make(embedder, library, arxiv_with(ok)).aretrieve(Q, 10, use_arxiv=True)).sources
    assert {s["source"] for s in both} == {"local", "arxiv"}
    strict = make(embedder, library, arxiv_with(ok), min_score_arxiv=0.999)
    only_lib = asyncio.run(strict.aretrieve(Q, 10, use_arxiv=True)).sources
    assert only_lib and {s["source"] for s in only_lib} == {"local"}
    lax = ResearchRetriever(embedder=embedder, library=library, arxiv=arxiv_with(ok), min_score=0.999,
                            min_score_arxiv=0.0)  # fmt: skip
    only_arxiv = asyncio.run(lax.aretrieve(Q, 10, use_arxiv=True)).sources
    assert only_arxiv and {s["source"] for s in only_arxiv} == {"arxiv"}
