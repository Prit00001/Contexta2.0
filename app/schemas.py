from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ASK_REQUEST_EXAMPLE = {"question": "How does retrieval-augmented generation reduce hallucination?", "top_k": 3,
                       "use_arxiv": True}


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


class CitationCheck(BaseModel):
    verification: Literal["marker_only"] = Field(
        "marker_only",
        description="Markers are checked against retrieved sources; whether a source entails a claim is NOT verified",
    )
    valid_markers: int = 0
    removed_markers: list[int] = Field([], description="Hallucinated [n] numbers removed from the answer")
    uncited_claims: int = 0


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
