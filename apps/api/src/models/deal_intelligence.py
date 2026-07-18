"""Versioned deal-room documents and deterministic evidence intelligence.

The records in this module are deliberately append-only.  A document upload, Q&A run,
extraction candidate, human review, comparison, or evaluation is an evidence artifact; a later
decision therefore creates another row instead of rewriting the historical record.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, Session, attributes, mapped_column

from src.db.base import Base, UUIDMixin, now_utc


class DataRoomDocument(UUIDMixin, Base):
    """One immutable version of a logical data-room document."""

    __tablename__ = "data_room_documents"
    __table_args__ = (
        UniqueConstraint(
            "deal_id", "logical_document_id", "version", name="uq_data_room_document_version"
        ),
        CheckConstraint("version >= 1", name="ck_data_room_document_version"),
        CheckConstraint("byte_size > 0", name="ck_data_room_document_nonempty"),
        Index("ix_data_room_documents_deal_logical", "deal_id", "logical_document_id"),
        Index("ix_data_room_documents_deal_created", "deal_id", "created_at"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logical_document_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_document_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("data_room_documents.id", ondelete="RESTRICT"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    filename: Mapped[str] = mapped_column(String(240), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    extension: Mapped[str] = mapped_column(String(12), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    document_metadata: Mapped[dict] = mapped_column("metadata_json", JSON, nullable=False, default=dict)
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="data_room")
    uploaded_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class DataRoomChunk(UUIDMixin, Base):
    """Extracted text with a resolvable page/paragraph or sheet/cell locator."""

    __tablename__ = "data_room_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_data_room_chunk_ordinal"),
        Index("ix_data_room_chunks_deal_document", "deal_id", "document_id", "ordinal"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("data_room_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    locator_type: Mapped[str] = mapped_column(String(20), nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class CitedQARun(UUIDMixin, Base):
    """A persisted deterministic answer or explicit abstention."""

    __tablename__ = "cited_qa_runs"
    __table_args__ = (
        CheckConstraint("status IN ('answered','abstained')", name="ck_cited_qa_status"),
        Index("ix_cited_qa_runs_deal_created", "deal_id", "created_at"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    retrieval_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    answer_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class StructuredClaim(UUIDMixin, Base):
    """An immutable revision of an extracted and human-reviewable structured claim."""

    __tablename__ = "structured_claims"
    __table_args__ = (
        UniqueConstraint("logical_claim_id", "revision", name="uq_structured_claim_revision"),
        CheckConstraint("revision >= 1", name="ck_structured_claim_revision"),
        CheckConstraint(
            "category IN ('debt_term','customer','contract','kpi','qoe_candidate')",
            name="ck_structured_claim_category",
        ),
        CheckConstraint(
            "review_status IN ('unreviewed','approved','rejected')",
            name="ck_structured_claim_review_status",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_claim_confidence"),
        Index("ix_structured_claims_deal_status", "deal_id", "review_status"),
        Index("ix_structured_claims_logical_revision", "logical_claim_id", "revision"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logical_claim_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_claim_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("structured_claims.id", ondelete="RESTRICT"), nullable=True
    )
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_room_documents.id", ondelete="RESTRICT"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_room_chunks.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    period: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_span: Mapped[dict] = mapped_column(JSON, nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unreviewed")
    extraction_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class ClaimReviewEvent(UUIDMixin, Base):
    """Append-only link between two claim revisions created by a human review action."""

    __tablename__ = "claim_review_events"
    __table_args__ = (
        UniqueConstraint("logical_claim_id", "to_revision", name="uq_claim_review_to_revision"),
        CheckConstraint("action IN ('approve','reject','edit')", name="ck_claim_review_action"),
        Index("ix_claim_reviews_logical_created", "logical_claim_id", "created_at"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logical_claim_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    from_claim_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("structured_claims.id", ondelete="RESTRICT"), nullable=False
    )
    to_claim_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("structured_claims.id", ondelete="RESTRICT"), nullable=False
    )
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    prior_status: Mapped[str] = mapped_column(String(20), nullable=False)
    resulting_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class RedactionProposal(UUIDMixin, Base):
    """A four-eyes, span-level redaction proposal against ONE immutable document version (G75).

    ``document_id`` pins the SPECIFIC version the spans were authored against; ``spans`` is a
    JSON list of ``{chunk_id, start, end, reason}`` where ``start``/``end`` are character
    offsets into that chunk's immutable ``DataRoomChunk.text``. Per-chunk addressing is
    deliberate: chunk text is the canonical serveable surface (QA citations, claim
    ``source_span``s, and locators all address it), so redaction spans reuse the same exact
    coordinate system instead of inventing a synthetic whole-document offset space that no
    read surface serves.

    Decisions follow the append-only discipline of this module in spirit: the row is written
    once as ``proposed`` and permits exactly ONE ``proposed -> approved|rejected`` transition
    (which stamps the decision fields), plus the set-once ``redacted_document_id`` link written
    by the approval transaction. Everything else — including re-deciding or editing spans — is
    rejected at flush time by :func:`_guard_redaction_proposal_update`.
    """

    __tablename__ = "redaction_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','approved','rejected')",
            name="ck_redaction_proposal_status",
        ),
        # Database-level backstop for the four-eyes decision invariants the service and ORM
        # guard enforce: a terminal status must carry its decider stamp, and the decider can
        # never be the proposer. Keeps the invariants true even for a writer that bypasses
        # ``decide_redaction``.
        CheckConstraint(
            "status = 'proposed' OR (decided_by_actor_id IS NOT NULL AND decided_at IS NOT "
            "NULL AND decided_by_actor_id <> proposed_by_actor_id)",
            name="ck_redaction_proposal_decided_fields",
        ),
        Index("ix_redaction_proposals_deal_status", "deal_id", "status"),
        Index("ix_redaction_proposals_deal_logical", "deal_id", "logical_document_id"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("data_room_documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    logical_document_id: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    spans: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_by_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    decided_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redacted_document_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("data_room_documents.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class DocumentComparison(UUIDMixin, Base):
    """Persisted change/contradiction analysis between two immutable document versions."""

    __tablename__ = "document_comparisons"
    __table_args__ = (
        CheckConstraint(
            "comparison_type IN ('change','contradiction')", name="ck_document_comparison_type"
        ),
        Index("ix_document_comparisons_deal_created", "deal_id", "created_at"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_room_documents.id", ondelete="RESTRICT"), nullable=False
    )
    to_document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_room_documents.id", ondelete="RESTRICT"), nullable=False
    )
    comparison_type: Mapped[str] = mapped_column(String(20), nullable=False)
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class SecFilingComparison(UUIDMixin, Base):
    """Immutable section/chunk diff between two SEC filings in one workspace."""

    __tablename__ = "sec_filing_comparisons"
    __table_args__ = (
        Index("ix_sec_filing_comparisons_workspace_created", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_filing_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("filings.id", ondelete="RESTRICT"), nullable=False
    )
    to_filing_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("filings.id", ondelete="RESTRICT"), nullable=False
    )
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class IntelligenceEvaluation(UUIDMixin, Base):
    """Reproducible guardrail evaluation over cited Q&A cases."""

    __tablename__ = "intelligence_evaluations"
    __table_args__ = (Index("ix_intelligence_evaluations_deal_created", "deal_id", "created_at"),)

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cases: Mapped[list] = mapped_column(JSON, nullable=False)
    qa_run_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


def _reject_immutable_mutation(mapper, connection, target) -> None:  # pragma: no cover - hook
    del mapper, connection
    raise ValueError(f"{type(target).__name__} records are append-only")


for _immutable_model in (
    DataRoomDocument,
    DataRoomChunk,
    CitedQARun,
    StructuredClaim,
    ClaimReviewEvent,
    DocumentComparison,
    SecFilingComparison,
    IntelligenceEvaluation,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_mutation)
    event.listen(_immutable_model, "before_delete", _reject_immutable_mutation)


_REDACTION_DECISION_FIELDS = frozenset(
    {"status", "decision_note", "decided_by_actor_id", "decided_at"}
)


def _guard_redaction_proposal_update(mapper, connection, target) -> None:
    """Permit exactly one ``proposed -> approved|rejected`` decision; reject every other UPDATE.

    Two flushes are legitimate: (1) the decision itself — status leaves ``proposed`` for a
    terminal value together with the decision fields (the approval transaction may include the
    ``redacted_document_id`` link); (2) the approval path's set-once ``redacted_document_id``
    fill from NULL after the version was minted. Editing spans/provenance, re-deciding a
    decided proposal, or rewriting an existing link all raise, keeping decided proposals final.
    """
    del connection
    changed: set[str] = set()
    prior_status = target.status
    for attribute in mapper.column_attrs:
        history = attributes.get_history(target, attribute.key)
        if not history.has_changes():
            continue
        changed.add(attribute.key)
        if attribute.key == "status" and history.deleted:
            prior_status = history.deleted[0]
    if not changed:
        return
    if changed == {"redacted_document_id"} and target.status == "approved":
        link_history = attributes.get_history(target, "redacted_document_id")
        if not link_history.deleted or link_history.deleted[0] is None:
            return
    if (
        "status" in changed
        and changed <= (_REDACTION_DECISION_FIELDS | {"redacted_document_id"})
        and prior_status == "proposed"
        and target.status in {"approved", "rejected"}
    ):
        return
    raise ValueError(
        "RedactionProposal decisions are final: only one proposed -> approved/rejected "
        "transition (and its set-once redacted-version link) is permitted"
    )


event.listen(RedactionProposal, "before_update", _guard_redaction_proposal_update)
event.listen(RedactionProposal, "before_delete", _reject_immutable_mutation)


# Mapper-level before_update/before_delete hooks only fire for ORM unit-of-work flushes; a Core
# ``update()``/``delete()`` executed through the session would bypass them. Mirror the sibling
# modules (evidence, underwriting_data, underwriting_model) with a session-level guard so bulk
# statements against these tables are rejected too. RedactionProposal is included: its single
# legitimate transition flows through instance flushes (guarded above), so ANY bulk statement
# against it is illegitimate by construction.
_BULK_GUARDED_TABLENAMES = frozenset(
    model.__tablename__
    for model in (
        DataRoomDocument,
        DataRoomChunk,
        CitedQARun,
        StructuredClaim,
        ClaimReviewEvent,
        DocumentComparison,
        SecFilingComparison,
        IntelligenceEvaluation,
        RedactionProposal,
    )
)


@event.listens_for(Session, "do_orm_execute")
def _reject_bulk_immutable_mutation(execute_state) -> None:  # pragma: no cover - integration tested
    if not (execute_state.is_update or execute_state.is_delete):
        return
    table = getattr(execute_state.statement, "table", None)
    if table is not None and table.name in _BULK_GUARDED_TABLENAMES:
        raise ValueError(f"{table.name} records are append-only; bulk statements are rejected")
