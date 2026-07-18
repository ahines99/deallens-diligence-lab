"""API contracts for the deal-room evidence intelligence surface."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.common import ORMModel

ClaimCategory = Literal["debt_term", "customer", "contract", "kpi", "qoe_candidate"]
ReviewAction = Literal["approve", "reject", "edit"]
ReviewStatus = Literal["unreviewed", "approved", "rejected"]
RedactionStatus = Literal["proposed", "approved", "rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentTextCreate(StrictModel):
    filename: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=20_000_000)
    title: str | None = Field(default=None, max_length=240)
    logical_document_id: str | None = Field(default=None, min_length=1, max_length=32)
    content_type: str | None = Field(default=None, max_length=160)
    document_metadata: dict[str, Any] = Field(default_factory=dict)


class DataRoomDocumentOut(ORMModel):
    id: str
    deal_id: str
    logical_document_id: str
    version: int
    supersedes_document_id: str | None
    title: str
    filename: str
    original_filename: str
    extension: str
    content_type: str
    sha256: str
    byte_size: int
    document_metadata: dict[str, Any]
    source_kind: str
    uploaded_by_actor_id: str | None
    created_at: datetime


class DataRoomChunkOut(ORMModel):
    id: str
    deal_id: str
    document_id: str
    ordinal: int
    locator_type: str
    locator: dict[str, Any]
    text: str
    content_hash: str
    char_count: int
    created_at: datetime


class QAFilters(StrictModel):
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    logical_document_ids: list[str] = Field(default_factory=list, max_length=100)
    extensions: list[str] = Field(default_factory=list, max_length=20)
    versions: list[int] = Field(default_factory=list, max_length=100)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    latest_only: bool = True

    @field_validator("versions")
    @classmethod
    def positive_versions(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("versions must be positive")
        return values


class CitedQARequest(StrictModel):
    question: str = Field(min_length=2, max_length=2_000)
    filters: QAFilters = Field(default_factory=QAFilters)


class CitationOut(StrictModel):
    document_id: str
    logical_document_id: str
    document_version: int
    filename: str
    sha256: str
    chunk_id: str
    content_hash: str
    locator: dict[str, Any]
    quote: str


class CitedQARunOut(ORMModel):
    id: str
    deal_id: str
    question: str
    filters: dict[str, Any]
    status: Literal["answered", "abstained"]
    answer: str
    citations: list[CitationOut]
    retrieval_metadata: dict[str, Any]
    answer_hash: str
    algorithm_version: str
    created_by_actor_id: str | None
    created_at: datetime


class ExtractionRequest(StrictModel):
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    categories: list[ClaimCategory] = Field(
        default_factory=lambda: [
            "debt_term", "customer", "contract", "kpi", "qoe_candidate"
        ],
        min_length=1,
    )
    min_confidence: float = Field(default=0.65, ge=0, le=1)
    latest_only: bool = True

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, values: list[ClaimCategory]) -> list[ClaimCategory]:
        return list(dict.fromkeys(values))


class StructuredClaimOut(ORMModel):
    id: str
    deal_id: str
    logical_claim_id: str
    revision: int
    supersedes_claim_id: str | None
    document_id: str
    chunk_id: str
    category: ClaimCategory
    field_name: str
    value_text: str
    value_number: float | None
    unit: str | None
    period: str | None
    currency: str | None
    confidence: float
    source_locator: dict[str, Any]
    source_span: dict[str, Any]
    review_status: ReviewStatus
    extraction_version: str
    created_by_actor_id: str | None
    created_at: datetime


class ClaimReviewRequest(StrictModel):
    action: ReviewAction
    expected_revision: int = Field(ge=1)
    note: str = Field(default="", max_length=4_000)
    field_name: str | None = Field(default=None, min_length=1, max_length=100)
    value_text: str | None = Field(default=None, min_length=1, max_length=10_000)
    value_number: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    period: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def edit_has_a_change(self) -> "ClaimReviewRequest":
        editable = (
            self.field_name,
            self.value_text,
            self.value_number,
            self.unit,
            self.period,
            self.currency,
            self.confidence,
        )
        if self.action == "edit" and all(value is None for value in editable):
            raise ValueError("edit requires at least one changed claim field")
        if self.action != "edit" and any(value is not None for value in editable):
            raise ValueError("claim fields can only be supplied for an edit")
        return self


class ClaimReviewOut(ORMModel):
    id: str
    deal_id: str
    logical_claim_id: str
    from_claim_id: str
    to_claim_id: str
    from_revision: int
    to_revision: int
    action: ReviewAction
    prior_status: ReviewStatus
    resulting_status: ReviewStatus
    changes: dict[str, Any]
    note: str
    reviewer_actor_id: str | None
    created_at: datetime


class ClaimReviewResult(StrictModel):
    claim: StructuredClaimOut
    review: ClaimReviewOut


class ClaimCollectionOut(StrictModel):
    approved: list[StructuredClaimOut]
    pending: list[StructuredClaimOut]
    rejected: list[StructuredClaimOut]
    counts: dict[str, int]


class ClaimHistoryOut(StrictModel):
    logical_claim_id: str
    revisions: list[StructuredClaimOut]
    reviews: list[ClaimReviewOut]


class RedactionSpanIn(StrictModel):
    """One span to redact: character offsets into the addressed chunk's immutable text."""

    chunk_id: str = Field(min_length=1, max_length=32)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def ordered_span(self) -> "RedactionSpanIn":
        if self.end <= self.start:
            raise ValueError("span end must be greater than span start")
        return self


class RedactionProposalCreate(StrictModel):
    spans: list[RedactionSpanIn] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4_000)


class RedactionDecisionRequest(StrictModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=4_000)


class RedactionSpanOut(StrictModel):
    chunk_id: str
    start: int
    end: int
    reason: str = ""


class RedactionProposalOut(ORMModel):
    id: str
    deal_id: str
    document_id: str
    logical_document_id: str
    document_version: int
    spans: list[RedactionSpanOut]
    status: RedactionStatus
    note: str
    decision_note: str
    proposed_by_actor_id: str
    decided_by_actor_id: str | None
    decided_at: datetime | None
    redacted_document_id: str | None
    created_at: datetime


class RedactionDecisionResult(StrictModel):
    proposal: RedactionProposalOut
    redacted_document: DataRoomDocumentOut | None = None


class ComparisonRequest(StrictModel):
    from_document_id: str = Field(min_length=1, max_length=32)
    to_document_id: str = Field(min_length=1, max_length=32)
    comparison_type: Literal["change", "contradiction"] = "change"

    @model_validator(mode="after")
    def distinct_documents(self) -> "ComparisonRequest":
        if self.from_document_id == self.to_document_id:
            raise ValueError("comparison documents must be different")
        return self


class ComparisonFinding(StrictModel):
    finding_type: str
    summary: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    shared_terms: list[str] = Field(default_factory=list)


class DocumentComparisonOut(ORMModel):
    id: str
    deal_id: str
    from_document_id: str
    to_document_id: str
    comparison_type: Literal["change", "contradiction"]
    findings: list[ComparisonFinding]
    finding_count: int
    algorithm_version: str
    created_by_actor_id: str | None
    created_at: datetime


class SecFilingComparisonRequest(StrictModel):
    from_filing_id: str = Field(min_length=1, max_length=32)
    to_filing_id: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_filings(self) -> "SecFilingComparisonRequest":
        if self.from_filing_id == self.to_filing_id:
            raise ValueError("SEC filings must be different")
        return self


class SecFilingComparisonOut(ORMModel):
    id: str
    workspace_id: str
    from_filing_id: str
    to_filing_id: str
    findings: list[ComparisonFinding]
    finding_count: int
    algorithm_version: str
    created_by_actor_id: str | None
    created_at: datetime


class EvaluationCase(StrictModel):
    question: str = Field(min_length=2, max_length=2_000)
    should_abstain: bool = False
    expected_answer_contains: list[str] = Field(default_factory=list, max_length=20)
    filters: QAFilters = Field(default_factory=QAFilters)


class EvaluationRequest(StrictModel):
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)
    minimum_numeric_traceability: float = Field(default=1.0, ge=0, le=1)
    minimum_citation_resolution: float = Field(default=0.95, ge=0, le=1)
    minimum_abstention_accuracy: float = Field(default=0.9, ge=0, le=1)


class IntelligenceEvaluationOut(ORMModel):
    id: str
    deal_id: str
    cases: list[dict[str, Any]]
    qa_run_ids: list[str]
    metrics: dict[str, Any]
    passed: bool
    algorithm_version: str
    created_by_actor_id: str | None
    created_at: datetime
