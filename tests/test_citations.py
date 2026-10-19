"""T-07: citation validation and claim attribution (FR-05, FR-06, defect D-05)."""

from __future__ import annotations

from core.citations import normalise_markers, split_claims, strip_invalid_markers, validate_citations

SOURCES = [
    {
        "id": "doc_004",
        "document_id": "doc_004",
        "chunk_id": "doc_004#0",
        "title": "RAG",
        "url": "https://arxiv.org/abs/2005.11401",
    },
    {
        "id": "arxiv_2004.04906",
        "document_id": "arxiv:2004.04906",
        "chunk_id": "arxiv:2004.04906#0",
        "title": "DPR",
        "url": "",
    },
]


def test_normalise_grouped_markers():
    assert normalise_markers("X [1, 2] and Y [1;3].") == "X [1][2] and Y [1][3]."


def test_strip_invalid_markers_reports_removed():
    text, removed = strip_invalid_markers("A [1] B [9] C [0] D [2][7].", 2)
    assert text == "A [1] B C D [2]." and removed == [0, 7, 9]


def test_valid_answer_report():
    r = validate_citations("RAG retrieves passages [1]. DPR uses two encoders [2][1]. It is popular.", SOURCES)
    assert [c["marker"] for c in r.citations] == [1, 2]
    assert r.citations[1] == {
        "marker": 2,
        "source_id": "arxiv_2004.04906",
        "document_id": "arxiv:2004.04906",
        "chunk_id": "arxiv:2004.04906#0",
        "title": "DPR",
        "url": "",
    }
    assert [c["status"] for c in r.claims] == ["cited", "cited", "uncited"]
    assert r.claims[1] == {"text": "DPR uses two encoders.", "markers": [2, 1], "status": "cited"}
    assert r.summary() == {
        "verification": "marker_only",
        "valid_markers": 2,
        "removed_markers": [],
        "uncited_claims": 1,
    }
    assert r.usable(has_sources=True)


def test_hallucinated_markers_removed_and_merged_with_earlier_removals():
    r = validate_citations("Claim [5]. Other claim [1].", SOURCES, already_removed=[9])
    assert r.answer == "Claim. Other claim [1]." and r.removed_markers == [5, 9]
    assert r.claims[0]["status"] == "uncited"


def test_answer_without_valid_citations_is_unusable():
    assert not validate_citations("A fluent answer with no citations.", SOURCES).usable(has_sources=True)
    assert not validate_citations("Only a fake one [8].", SOURCES).usable(has_sources=True)
    assert not validate_citations("   ", SOURCES).usable(has_sources=True)


def test_markers_after_punctuation_stay_with_their_sentence():
    claims = split_claims("First fact.[1] Second fact.[2][1] Third.")
    assert [(c["text"], c["markers"]) for c in claims] == [
        ("First fact.", [1]),
        ("Second fact.", [2, 1]),
        ("Third.", []),
    ]


def test_paragraphs_split_into_claims():
    claims = split_claims("Para one [1].\n\nPara two [2].")
    assert len(claims) == 2 and all(c["status"] == "cited" for c in claims)


def test_fullwidth_brackets_are_citations():
    # verbatim gpt-oss-120b output (eval q003): 【1】 was missed, so a cited answer looked uncited
    report = validate_citations("GPT‑3 is a 175‑billion‑parameter autoregressive language model【1】.", [{"id": "a"}])
    assert report.valid_markers == 1 and report.answer.endswith("model[1].")
    assert normalise_markers("x ［2］ y【１，３】") == "x [2] y[1][3]"
