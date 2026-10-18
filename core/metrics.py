"""Prometheus metrics (P2-FR-07), exposed at ``GET /metrics`` (``METRICS_ENABLED``, default on).

Labels are fixed, low-cardinality enums (endpoint, outcome, stage, status, provider). Metrics never
carry user content: no question text, answers, client IPs or request IDs.

Values are per process; scrape every instance (Prometheus sums them), as with any multi-instance
deployment.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

ANSWERS = Counter(
    "contexta_answers_total",
    "Answered research requests by endpoint, outcome and whether the answer came from cache.",
    ["endpoint", "outcome", "cached"],
)
ANSWER_SECONDS = Histogram(
    "contexta_answer_seconds",
    "End-to-end answer latency (uncached requests only).",
    ["endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30),
)
STAGE_SECONDS = Histogram(
    "contexta_workflow_stage_seconds",
    "Duration of each LangGraph workflow stage, by stage and status.",
    ["stage", "status"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15),
)
LLM_CALLS = Counter("contexta_llm_calls_total", "LLM provider calls by provider and status.", ["provider", "status"])
RATE_LIMITED = Counter("contexta_rate_limited_total", "Requests rejected with 429, by endpoint.", ["endpoint"])


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
