from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASK_REQUEST_EXAMPLE = {
    "question": "How does retrieval-augmented generation reduce hallucination?",
    "top_k": 3,
    "use_arxiv": True,
}


class Turn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field("", max_length=4000)


class AskRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [ASK_REQUEST_EXAMPLE]})

    question: str = Field(..., min_length=3, max_length=500, description="The research question")
    top_k: int = Field(5, ge=1, le=10, description="Maximum number of sources to use")
    use_arxiv: bool = Field(True, description="Also search arXiv live (ignored if ARXIV_ENABLED=false)")
    history: list[Turn] = Field(
        [],
        max_length=3,
        description="Previous turns (oldest first) when `question` is a follow-up. Used only to rewrite the "
        "follow-up into a standalone question for this request; never stored or logged by the server.",
    )


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
        None, description="Unless CLAIM_CHECK=off: does the cited text back this claim? (see support_method)"
    )
    support_method: Optional[Literal["quote", "numbers", "llm_judge"]] = Field(
        None,
        description="quote: claim appears verbatim in a cited source; numbers: it states a figure none of its "
        "cited sources contain; llm_judge: decided by an LLM (CLAIM_CHECK=llm)",
    )
    unsupported_numbers: Optional[list[str]] = Field(
        None, description="With support_method=numbers: the figures not found in the cited sources"
    )


class CitationCheck(BaseModel):
    verification: Literal["marker_only", "local_checks", "claim_support"] = Field(
        "marker_only",
        description="marker_only: markers are checked against retrieved sources, entailment is NOT verified. "
        "local_checks: cited claims were also checked for verbatim quotes and figures their sources lack. "
        "claim_support: as local_checks, plus an LLM judge for the rest (see claims[].support)",
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
    standalone_question: Optional[str] = Field(
        None, description="With `history`: the follow-up rewritten as a standalone question (what was searched)"
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
    history: list[Turn] = Field(
        [],
        max_length=3,
        description="Stateless follow-up context, as in /ask. Not allowed together with `conversation_id`, "
        "whose stored turns are used instead.",
    )
    conversation_id: Optional[UUID] = Field(
        None,
        description="Continue a stored conversation (P2-14): its last turns are the follow-up context and this "
        "turn is appended. Only the user who started it can use it.",
    )
    save: bool = Field(
        False,
        description="Start a stored conversation with this turn; the response's `conversation_id` continues it. "
        "Needs a signed-in user (not an API key). Kept for HISTORY_RETENTION_DAYS after the last turn.",
    )

    @model_validator(mode="after")
    def _one_kind_of_history(self):
        if self.conversation_id and self.history:
            raise ValueError("send either `conversation_id` or `history`, not both")
        if self.conversation_id and self.save:
            raise ValueError("`save` starts a new conversation; omit it when sending `conversation_id`")
        return self


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
    standalone_question: Optional[str] = Field(
        None, description="With `history`: the follow-up rewritten as a standalone question (what was searched)"
    )
    conversation_id: Optional[str] = Field(None, description="The stored conversation this turn was saved to")


# ---- Stored conversations (P2-14, ADR-006) ----


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    turns: int


class StoredSource(BaseModel):
    document_id: str
    filename: str
    page_number: Optional[int] = None
    section_heading: Optional[str] = None
    text: str = Field(..., description="The full passage the answer was generated from, as it was at the time")
    score: float


class ConversationTurnOut(BaseModel):
    position: int
    question: str
    standalone_question: Optional[str] = None
    answer: str
    outcome: str
    sources: list[StoredSource] = []
    citations: list[CitationOut] = []
    claims: list[ClaimOut] = []
    created_at: str


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    expires_at: str = Field(..., description="When this conversation is deleted unless another turn is added")
    turns: list[ConversationTurnOut] = []


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


class WorkspaceOut(BaseModel):
    workspace_id: str
    name: str
    role: str
    org_id: str


class AuthConfig(BaseModel):
    """What the /workspace page needs to sign users in. Only public values."""

    workspaces_enabled: bool
    supabase_url: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    history_retention_days: int


class BillingOut(BaseModel):
    """A workspace's plan (ADR-007). `plan`: trial | active | past_due | expired; `unlimited` when billing is off."""

    enabled: bool
    plan: Literal["unlimited", "trial", "active", "past_due", "expired"]
    trial_seconds_left: int = 0
    trial_seconds_total: int = 0
    seats: int = Field(0, description="Members now, i.e. what the next renewal bills")
    seats_billed: Optional[int] = None
    subscription_status: Optional[str] = None
    current_end: Optional[str] = None
    price_label: str = ""
    can_manage: bool = False


class SubscribeOut(BaseModel):
    subscription_id: str
    key_id: str = Field(..., description="Razorpay public key id for Checkout")
    seats: int


class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str = Field(..., max_length=100)
    razorpay_subscription_id: str = Field(..., max_length=100)
    razorpay_signature: str = Field(..., max_length=200)


class MemberOut(BaseModel):
    user_id: str
    role: str
    created_at: str
    email: Optional[str] = None


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
