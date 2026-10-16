"""Citation validation and claim attribution (FR-05, FR-06, defect D-05).

What "validated" means here - deliberately narrow and stated in every response as
``verification: "marker_only"``:

* a citation marker ``[n]`` is **valid** when ``n`` refers to a source that was actually
  retrieved and returned to the client; invalid markers are removed and reported;
* each answer sentence is a **claim** whose status is ``cited`` (has >= 1 valid marker) or
  ``uncited``.

It does *not* check that the cited passage entails the claim; that needs an NLI/LLM judge and
is Phase 2 work. Clients must not present ``cited`` as "verified true".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.text import split_sentences

_GROUP = re.compile(r"\[(\s*\d+(?:\s*[,;]\s*\d+)+\s*)\]")  # [1, 2] / [1;3]
_MARKER = re.compile(r"\[(\d+)\]")
_MARKERS_AFTER_PUNCT = re.compile(r"([.!?])((?:\s*\[\d+\])+)")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")


def normalise_markers(text: str) -> str:
    """Rewrite grouped markers like ``[1, 3]`` as ``[1][3]`` (the format the UI links)."""
    return _GROUP.sub(lambda m: "".join(f"[{n.strip()}]" for n in re.split(r"[,;]", m.group(1))), text)


def strip_invalid_markers(text: str, n_sources: int) -> tuple[str, list[int]]:
    """Remove markers outside 1..n_sources. Returns (clean text, sorted removed marker numbers)."""
    removed: set[int] = set()

    def keep(m: re.Match) -> str:
        n = int(m.group(1))
        if 1 <= n <= n_sources:
            return m.group(0)
        removed.add(n)
        return ""

    out = _MARKER.sub(keep, normalise_markers(text))
    if removed:
        out = _SPACE_BEFORE_PUNCT.sub(r"\1", re.sub(r"[ \t]{2,}", " ", out)).strip()
    return out, sorted(removed)


@dataclass
class CitationReport:
    answer: str
    citations: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    removed_markers: list[int] = field(default_factory=list)
    verification: str = "marker_only"

    @property
    def valid_markers(self) -> int:
        return len(self.citations)

    @property
    def uncited_claims(self) -> int:
        return sum(1 for c in self.claims if c["status"] == "uncited")

    def usable(self, has_sources: bool) -> bool:
        """An LLM answer is usable if it is non-empty and, when sources exist, cites at least one."""
        return bool(self.answer.strip()) and (self.valid_markers > 0 or not has_sources)

    def summary(self) -> dict:
        return {
            "verification": self.verification,
            "valid_markers": self.valid_markers,
            "removed_markers": self.removed_markers,
            "uncited_claims": self.uncited_claims,
        }


def split_claims(answer: str) -> list[dict]:
    # "...works.[1] Next" -> "...works [1]. Next" so markers stay with the sentence they follow.
    text = _MARKERS_AFTER_PUNCT.sub(lambda m: f" {m.group(2).strip()}{m.group(1)}", answer)
    claims = []
    for paragraph in re.split(r"\n{2,}", text):
        for sent in split_sentences(paragraph):
            markers = [int(n) for n in _MARKER.findall(sent)]
            plain = _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join(_MARKER.sub(" ", sent).split()))
            if not plain:
                continue
            claims.append(
                {
                    "text": plain,
                    "markers": sorted(set(markers), key=markers.index),
                    "status": "cited" if markers else "uncited",
                }
            )
    return claims


def validate_citations(answer: str, sources: list[dict], already_removed: list[int] | None = None) -> CitationReport:
    """Validate ``answer`` against the numbered ``sources`` (1-based, in prompt order)."""
    clean, removed = strip_invalid_markers(answer, len(sources))
    removed = sorted(set(removed) | set(already_removed or []))
    seen: list[int] = []
    for m in _MARKER.findall(clean):
        n = int(m)
        if n not in seen:
            seen.append(n)
    citations = []
    for n in seen:
        s = sources[n - 1]
        citations.append(
            {
                "marker": n,
                "source_id": s.get("id", ""),
                "document_id": s.get("document_id", s.get("id", "")),
                "chunk_id": s.get("chunk_id", ""),
                "title": s.get("title", ""),
                "url": s.get("url", ""),
            }
        )
    return CitationReport(answer=clean, citations=citations, claims=split_claims(clean), removed_markers=removed)
