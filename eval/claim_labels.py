"""Measuring the claim-support checker (PRD P2-FR-04) against human labels.

1. Produce a sheet (real LLM calls, CLAIM_CHECK=llm):

       CLAIM_CHECK=llm PYTHONPATH=. python eval/run_eval.py --with-llm
       -> eval/claims_to_label.csv  (one row per cited claim)

2. Open the CSV in a spreadsheet and fill ``human_label`` with ``supported`` or ``not_supported``
   for each row: does the ``cited_text`` (only that text, not your own knowledge) back the
   ``claim``? Leave a row empty to skip it. Aim for >= 100 labelled rows. Label without looking at
   ``checker_verdict`` if you can (hide the column) - otherwise the labels lean towards the checker.

3. Score it:

       PYTHONPATH=. python eval/run_eval.py --score-claims eval/claims_to_label.csv

Reported per support method (``quote`` = decided locally, ``llm_judge``) and overall: coverage
(share of rows the checker decided, i.e. not ``unknown``), accuracy on decided rows, precision of
``supported`` (when the checker says supported, how often you agree - the number that matters for
the UI not over-claiming) and recall of ``not_supported`` (of the claims you marked unsupported,
how many the checker caught).
"""

from __future__ import annotations

import csv
from pathlib import Path

FIELDS = [
    "question_id", "question", "claim_index", "claim", "markers", "cited_text",
    "checker_verdict", "checker_method", "human_label",
]  # fmt: skip
LABELS = ("supported", "not_supported")


def claim_rows(question: dict, state: dict) -> list[dict]:
    """Sheet rows for one answered question: cited claims only (uncited ones have nothing to check)."""
    report, sources = state.get("citation_report"), state.get("sources", [])
    rows = []
    for i, claim in enumerate(getattr(report, "claims", []) or []):
        markers = [m for m in claim.get("markers", []) if 1 <= m <= len(sources)]
        if not markers:
            continue
        cited = "\n\n".join(f"[{m}] {sources[m - 1].get('text', '')}" for m in markers)
        rows.append(
            {
                "question_id": question["id"],
                "question": question["question"],
                "claim_index": i,
                "claim": claim["text"],
                "markers": " ".join(str(m) for m in markers),
                "cited_text": cited,
                "checker_verdict": claim.get("support") or "",
                "checker_method": claim.get("support_method") or "",
                "human_label": "",
            }
        )
    return rows


def write_sheet(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _metrics(rows: list[dict]) -> dict:
    decided = [r for r in rows if r["checker_verdict"] in LABELS]
    said_supported = [r for r in decided if r["checker_verdict"] == "supported"]
    truly_unsupported = [r for r in rows if r["human_label"] == "not_supported"]

    def ratio(num: int, den: int):
        return round(num / den, 4) if den else None

    return {
        "n_labelled": len(rows),
        "coverage": ratio(len(decided), len(rows)),
        "accuracy": ratio(sum(r["checker_verdict"] == r["human_label"] for r in decided), len(decided)),
        "precision_supported": ratio(sum(r["human_label"] == "supported" for r in said_supported), len(said_supported)),
        "recall_not_supported": ratio(
            sum(r["checker_verdict"] == "not_supported" for r in truly_unsupported), len(truly_unsupported)
        ),
        "human_not_supported": len(truly_unsupported),
    }


def score_sheet(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("human_label", "").strip()]
    bad = sorted({r["human_label"] for r in rows if r["human_label"].strip() not in LABELS})
    if bad:
        raise SystemExit(f"human_label must be one of {list(LABELS)}; found {bad}")
    for r in rows:
        r["human_label"] = r["human_label"].strip()
    out = {"overall": _metrics(rows)}
    for method in sorted({r["checker_method"] for r in rows if r["checker_method"]}):
        out[method] = _metrics([r for r in rows if r["checker_method"] == method])
    return out


def markdown(scores: dict) -> str:
    lines = [
        "| Method | Labelled | Coverage | Accuracy | Precision (supported) | Recall (not_supported) |",
        "|---|---|---|---|---|---|",
    ]
    for method, m in scores.items():
        lines.append(
            f"| {method} | {m['n_labelled']} | {m['coverage']} | {m['accuracy']} | {m['precision_supported']} "
            f"| {m['recall_not_supported']} (of {m['human_not_supported']}) |"
        )
    if scores["overall"]["n_labelled"] < 100:
        lines.append(f"\nOnly {scores['overall']['n_labelled']} rows labelled; the PRD asks for >= 100.")
    return "\n".join(lines)
