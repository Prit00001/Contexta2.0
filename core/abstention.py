"""Recognising an LLM answer that declines because the sources don't cover the question.

The prompt (core.prompts, rule 2) tells the model to say so plainly, opening with
``ABSTAIN_PHRASE``. Such an answer has no citations - correctly, since nothing supports an answer -
so the citation rule alone would reject it, retry, and finally replace it with extractive quotes
from irrelevant sources. Measured with Groq gpt-oss-20b on the 33 unanswerable eval questions
(EVALUATION_REPORT section 5e): 28 ended that way before this check existed.

Only an answer with **no valid citation** can count as an abstention, so a cited answer that merely
mentions a limitation is never turned into one.
"""

from __future__ import annotations

import re

ABSTAIN_PHRASE = "The provided sources do not contain enough information"

_SUBJECT = r"(?:sources?|documents?|passages?|excerpts?|context|papers?|materials?)"
_NEGATION = r"(?:do(?:es)?\s+not|don['’]t|doesn['’]t|cannot|can['’]t|fail\s+to|lack|have\s+no|has\s+no)"
_VERB = r"(?:contain|include|provide|mention|cover|address|discuss|describe|explain|answer|say|have|support)"
_PATTERNS = [
    re.compile(re.escape(ABSTAIN_PHRASE), re.IGNORECASE),
    re.compile(rf"\b{_SUBJECT}\b[^.]{{0,80}}?\b{_NEGATION}\b[^.]{{0,40}}?\b{_VERB}", re.IGNORECASE),
    re.compile(rf"\bnone of the\b[^.]{{0,40}}?\b{_SUBJECT}\b[^.]{{0,60}}?\b{_VERB}", re.IGNORECASE),
    re.compile(r"\b(?:not enough|insufficient|no relevant|no)\s+information\b", re.IGNORECASE),
]


def is_abstention(answer: str, valid_citations: int) -> bool:
    """True when an uncited answer says the sources don't cover the question."""
    if valid_citations > 0 or not answer.strip():
        return False
    return any(p.search(answer) for p in _PATTERNS)
