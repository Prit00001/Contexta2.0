from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ASK_REQUEST_EXAMPLE = {
    "question": "How does retrieval-augmented generation reduce hallucination?",
    "top_k": 3,
    "use_arxiv": True,
}


class AskRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [ASK_REQUEST_EXAMPLE]})

    question: str = Field(..., min_length=3, max_length=500, description="The research question")
    top_k: int = Field(5, ge=1, le=10, description="Maximum number of sources to use")
    use_arxiv: bool = Field(True, description="Also search arXiv live (ignored if ARXIV_ENABLED=false)")


class Source(BaseModel):
    id: str
    title: str
    source: str  # "local" | "arxiv" | "pdf"
    url: str = ""  # empty for uploaded PDFs; always http(s) otherwise
    score: float
    snippet: str = ""
    authors: list[str] = []
    published: str = ""
    page: Optional[int] = None  # set for uploaded PDFs (Optional, not `int | None`, for Python 3.9)
    marker: int = Field(0, description="The [n] number that refers to this source in the answer")
    document_id: str = Field("", description="Paper / file this passage belongs to")
    chunk_id: str = Field("", description="The specific passage: <document_id>#<chunk index>")


class CitationOut(BaseModel):
    marker: int
    source_id: str
    document_id: str
    chunk_id: str
    title: str
    url: str = ""


class ClaimOut(BaseModel):
    text: str
    markers: list[int] = []
    status: Literal["cited", "uncited"]
    support: Optional[Literal["supported", "not_supported", "unknown"]] = Field(
        None, description="Only when CLAIM_CHECK=llm: does the cited text back this claim? (unmeasured LLM judge)"
    )
    support_method: Optional[Literal["quote", "llm_judge"]] = Field(
        None, description="quote: claim appears verbatim in a cited source; llm_judge: decided by an LLM"
    )


class CitationCheck(BaseModel):
    verification: Literal["marker_only", "claim_support"] = Field(
        "marker_only",
        description="marker_only: markers are checked against retrieved sources, entailment is NOT verified. "
        "claim_support: each cited claim was also checked against its cited text (see claims[].support)",
    )
    valid_markers: int = 0
    removed_markers: list[int] = Field([], description="Hallucinated [n] numbers removed from the answer")
    uncited_claims: int = 0
    supported_claims: Optional[int] = None
    not_supported_claims: Optional[int] = None
    unknown_claims: Optional[int] = None


class WorkflowStep(BaseModel):
    stage: str
    status: str
    duration_ms: int


class AskResponse(BaseModel):
    question: str
    answer: str
    sources: list[Source]
    total_sources: int
    model_used: str
    provider: str
    llm_used: bool
    cached: bool
    warnings: list[str] = []  # user-facing notes (arXiv down, PDF truncated, ...)
    diagnostics: list[str] = []  # provider errors for debugging; the UI logs these to the console
    timings_ms: dict[str, int] = {}
    # ---- added in Contexta 2.0 (additive; the baseline fields above are unchanged) ----
    request_id: str = ""
    outcome: Literal["answered", "extractive_fallback", "insufficient_evidence"] = "answered"
    citations: list[CitationOut] = []
    claims: list[ClaimOut] = []
    citation_check: CitationCheck = CitationCheck()
    workflow: list[WorkflowStep] = []
    query_rewrites: list[str] = Field(
        [], description="Search queries the rewriter produced (QUERY_REWRITE != off); empty if none were used"
    )


ASK_RESPONSE_EXAMPLE = {
    "question": "How does retrieval-augmented generation reduce hallucination?",
    "answer": "RAG conditions the generator on passages fetched from an external index, so answers are grounded "
    "in evidence rather than only in model parameters [1]. Dense retrievers such as DPR supply those passages [2].",
    "sources": [
        {"id": "doc_004", "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP", "source": "local",
         "url": "https://arxiv.org/abs/2005.11401", "score": 0.71, "snippet": "Retrieval-augmented generation (RAG) "
         "combines a parametric seq2seq generator with a non-parametric memory...", "authors": [], "published": "",
         "page": None, "marker": 1, "document_id": "doc_004", "chunk_id": "doc_004#0"},
        {"id": "doc_005", "title": "Dense Passage Retrieval for Open-Domain QA", "source": "local",
         "url": "https://arxiv.org/abs/2004.04906", "score": 0.52, "snippet": "Dense Passage Retrieval (DPR) trains "
         "two BERT encoders...", "authors": [], "published": "", "page": None, "marker": 2, "document_id": "doc_005",
         "chunk_id": "doc_005#0"},
    ],
    "total_sources": 2,
    "model_used": "openai/gpt-oss-20b",
    "provider": "groq",
    "llm_used": True,
    "cached": False,
    "warnings": [],
    "diagnostics": [],
    "timings_ms": {"local": 4, "arxiv": 0, "generate": 1180, "total": 1190},
    "request_id": "3f9c1a7e2b4d6f80",
    "outcome": "answered",
    "citations": [
        {"marker": 1, "source_id": "doc_004", "document_id": "doc_004", "chunk_id": "doc_004#0",
         "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP", "url": "https://arxiv.org/abs/2005.11401"},
        {"marker": 2, "source_id": "doc_005", "document_id": "doc_005", "chunk_id": "doc_005#0",
         "title": "Dense Passage Retrieval for Open-Domain QA", "url": "https://arxiv.org/abs/2004.04906"},
    ],
    "claims": [
        {"text": "RAG conditions the generator on passages fetched from an external index, so answers are grounded "
         "in evidence rather than only in model parameters.", "markers": [1], "status": "cited"},
        {"text": "Dense retrievers such as DPR supply those passages.", "markers": [2], "status": "cited"},
    ],
    "citation_check": {"verification": "marker_only", "valid_markers": 2, "removed_markers": [], "uncited_claims": 0},
    "workflow": [
        {"stage": "validate_request", "status": "ok", "duration_ms": 0},
        {"stage": "retrieve", "status": "ok", "duration_ms": 6},
        {"stage": "generate", "status": "ok", "duration_ms": 1175},
        {"stage": "validate_answer", "status": "ok", "duration_ms": 1},
        {"stage": "finalize", "status": "answered", "duration_ms": 0},
    ],
}  # fmt: skip


class ErrorResponse(BaseModel):
    detail: str
    request_id: str = ""


class HealthResponse(BaseModel):
    status: str
    index_size: int
    model: str
    embedding_backend: str
    llm_providers: list[str]
    arxiv_enabled: bool


class ReadyCheck(BaseModel):
    ok: bool
    detail: str


class ReadyResponse(BaseModel):
    """`ready` = all checks ok; `degraded` = serving with reduced quality; `not_ready` = HTTP 503."""

    status: Literal["ready", "degraded", "not_ready"]
    checks: dict[str, ReadyCheck]


# ---- Workspace document Q&A (Phase 1c) ----


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    status: str


class DocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    error: Optional[str] = None
    created_at: str


class Citation(BaseModel):
    document_id: str
    filename: str
    page_number: Optional[int] = None
    section_heading: Optional[str] = None
    snippet: str
    score: float


class WorkspaceAskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    top_k: int = Field(5, ge=1, le=10)


class WorkspaceAskResponse(BaseModel):
    question: str
    answer: str
    sources: list[Citation]
    provider: str
    model_used: str
    llm_used: bool
    warnings: list[str] = []
    # ---- added for P2-FR-06: same fields as AskResponse, same graph produces them ----
    outcome: Literal["answered", "extractive_fallback", "insufficient_evidence"] = "answered"
    citations: list[CitationOut] = []
    claims: list[ClaimOut] = []
    citation_check: CitationCheck = CitationCheck()
    workflow: list[WorkflowStep] = []
    query_rewrites: list[str] = Field(
        [], description="Search queries the rewriter produced (QUERY_REWRITE != off); empty if none were used"
    )


# ---- Workspace bootstrap, membership, API keys (Phase 1d) ----


class CreateWorkspaceRequest(BaseModel):
    org_name: str = Field(..., min_length=1, max_length=200)
    workspace_name: str = Field(..., min_length=1, max_length=200)


class CreateWorkspaceResponse(BaseModel):
    org_id: str
    workspace_id: str


class AddMemberRequest(BaseModel):
    email: str
    role: str = Field("member", pattern="^(admin|member|viewer)$")


class MemberOut(BaseModel):
    user_id: str
    role: str
    created_at: str


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str
    api_key: str  # shown once; not retrievable again


class ApiKeyOut(BaseModel):
    id: str
    name: str
    created_at: str
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None
