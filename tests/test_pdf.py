"""PDF chunking and validation. No network, no model download."""

import pytest
from pypdf.errors import PdfReadError

from core.pdf import chunk, extract_pages


def test_chunk_keeps_page_numbers_and_respects_size():
    sentence = "Low-rank adaptation trains small matrices instead of the full weights. "
    pages = [
        (1, sentence * 30),
        (2, "Retrieval grounds answers in external documents. " * 5),
    ]
    chunks = chunk(pages, size=400)
    assert {c["page"] for c in chunks} == {1, 2}
    assert all(len(c["text"]) <= 400 + len(sentence) for c in chunks)


def test_chunk_hard_splits_text_without_punctuation():
    table_like = " ".join(f"cell{i}" for i in range(500))  # no sentence breaks
    chunks = chunk([(3, table_like)], size=300)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 600 for c in chunks)


def test_chunk_drops_tiny_fragments_and_empty_pages():
    assert chunk([(1, "Too short."), (2, "")]) == []


def test_extract_pages_rejects_garbage():
    with pytest.raises(PdfReadError):
        extract_pages(b"%PDF-1.4 not really a pdf")
