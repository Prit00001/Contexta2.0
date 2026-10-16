"""T-06: source metadata validation and Document mapping (FR-04, FR-06, SR-35)."""

from __future__ import annotations

import pytest

from core.sources import from_document, normalize_source, safe_url, to_document


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://arxiv.org/abs/2005.11401", "https://arxiv.org/abs/2005.11401"),
        ("http://example.org/x", "http://example.org/x"),
        ("javascript:alert(1)", ""),
        ("file:///etc/passwd", ""),
        ("data:text/html,hi", ""),
        ("https://", ""),
        ("https://a b.com", ""),
        ("", ""),
        (None, ""),
        (42, ""),
        ("https://x.org/" + "a" * 3000, ""),
    ],
)
def test_safe_url(url, expected):
    assert safe_url(url) == expected


def test_local_document_ids():
    s = normalize_source({"id": "doc_004", "source": "local", "title": "RAG", "url": "https://arxiv.org/abs/2005.11401",
                          "text": "t", "score": 0.5})  # fmt: skip
    assert s["document_id"] == "doc_004" and s["chunk_id"] == "doc_004#0"


def test_arxiv_document_id_from_url_ignores_version():
    s = normalize_source({"id": "arxiv_2005.11401", "source": "arxiv", "title": "RAG",
                          "url": "https://arxiv.org/abs/2005.11401v4", "text": "t"})  # fmt: skip
    assert s["document_id"] == "arxiv:2005.11401" and s["chunk_id"] == "arxiv:2005.11401#0"


def test_pdf_chunk_ids_are_kept():
    s = normalize_source({"id": "pdf-3", "source": "pdf", "document_id": "pdf:abc", "chunk_id": "pdf:abc#3",
                          "title": "f.pdf · p. 2", "text": "t", "page": 2})  # fmt: skip
    assert (s["document_id"], s["chunk_id"], s["page"]) == ("pdf:abc", "pdf:abc#3", 2)


def test_malformed_metadata_is_repaired():
    s = normalize_source(
        {"id": None, "source": "weird", "title": "  \n ", "url": "javascript:x", "authors": "not a list",
         "score": float("nan"), "page": -1, "published": "2020-05-22T00:00:00Z", "text": None}
    )  # fmt: skip
    assert s["source"] == "local" and s["title"] == "Untitled source" and s["url"] == ""
    assert s["authors"] == [] and s["score"] == 0.0 and s["page"] is None
    assert s["published"] == "2020-05-22" and s["text"] == "" and s["document_id"] == "unknown"


def test_authors_filtered_and_capped():
    s = normalize_source({"id": "a", "authors": ["A", "", None, 3, *[f"P{i}" for i in range(20)]], "text": "t"})
    assert s["authors"][0] == "A" and len(s["authors"]) == 10 and all(isinstance(a, str) and a for a in s["authors"])


def test_document_round_trip():
    s = normalize_source({"id": "doc_001", "source": "local", "title": "T", "url": "https://x.org", "text": "body",
                          "score": 0.3})  # fmt: skip
    doc = to_document(s)
    assert doc.page_content == "body" and doc.id == "doc_001#0" and "text" not in doc.metadata
    assert from_document(doc) == s
