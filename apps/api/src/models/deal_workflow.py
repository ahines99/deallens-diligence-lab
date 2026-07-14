"""Institutional deal workflow and investment-committee persistence models.

The module is intentionally independent from the legacy ``Workspace`` lifecycle. A deal can
optionally point at a workspace while organization and fund ownership provide the tenant boundary
for all new workflow records.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin, now_utc


DEAL_STAGES = (
    "sourcing",
    "screening",
    "initial_review",
    "diligence",
    "ic_review",
    "signing",
    "closed",
    "declined",
)


class Organization(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_tenant_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    identity_provider: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Fund(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "funds"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_funds_org_name"),
        CheckConstraint("status IN ('active','inactive','realized')", name="ck_funds_status"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vintage_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    strategy: Mapped[str] = mapped_column(String(80), nullable=False, default="buyout")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Deal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_deals_org_code"),
        CheckConstraint(
            "stage IN ('sourcing','screening','initial_review','diligence','ic_review',"
            "'signing','closed','declined')",
            name="ck_deals_stage",
        ),
        CheckConstraint("status IN ('active','on_hold','closed','declined')", name="ck_deals_status"),
        Index("ix_deals_org_stage", "organization_id", "stage"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fund_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("funds.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_company: Mapped[str] = mapped_column(String(200), nullable=False)
    deal_type: Mapped[str] = mapped_column(String(40), nullable=False, default="buyout")
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default="sourcing")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    ic_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DealStageGate(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deal_stage_gates"
    __table_args__ = (
        UniqueConstraint("deal_id", "stage", "code", name="uq_deal_gate_stage_code"),
        CheckConstraint(
            "status IN ('pending','satisfied','waived')", name="ck_deal_stage_gates_status"
        ),
        Index("ix_deal_stage_gates_deal_stage", "deal_id", "stage"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DealStageTransition(UUIDMixin, Base):
    """Append-only record of every pipeline state transition."""

    __tablename__ = "deal_stage_transitions"
    __table_args__ = (
        UniqueConstraint("deal_id", "sequence", name="uq_deal_transition_sequence"),
        Index("ix_deal_transitions_deal_created", "deal_id", "created_at"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_stage: Mapped[str] = mapped_column(String(30), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(30), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class DealTeamMember(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deal_team_members"
    __table_args__ = (
        UniqueConstraint("deal_id", "actor_id", name="uq_deal_team_actor"),
        CheckConstraint(
            "role IN ('deal_lead','investment_partner','principal','associate','operating_partner',"
            "'finance','legal','advisor','observer')",
            name="ck_deal_team_role",
        ),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DealWorkstream(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deal_workstreams"
    __table_args__ = (
        UniqueConstraint("deal_id", "slug", name="uq_deal_workstream_slug"),
        CheckConstraint(
            "status IN ('not_started','in_progress','blocked','complete','waived')",
            name="ck_deal_workstreams_status",
        ),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_started")
    lead_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class DealMilestone(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deal_milestones"
    __table_args__ = (
        CheckConstraint(
            "status IN ('not_started','in_progress','blocked','complete','cancelled')",
            name="ck_deal_milestones_status",
        ),
        Index("ix_deal_milestones_deal_due", "deal_id", "due_date"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workstream_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deal_workstreams.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_started")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DealTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deal_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('not_started','in_progress','blocked','complete','cancelled')",
            name="ck_deal_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('low','medium','high','critical')", name="ck_deal_tasks_priority"
        ),
        Index("ix_deal_tasks_deal_status_due", "deal_id", "status", "due_date"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workstream_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deal_workstreams.id", ondelete="SET NULL"), nullable=True
    )
    milestone_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deal_milestones.id", ondelete="SET NULL"), nullable=True
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deal_tasks.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_started")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    assignee_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    dependency_task_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    blocked_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DiligenceRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "diligence_requests"
    __table_args__ = (
        UniqueConstraint("deal_id", "request_number", name="uq_diligence_request_number"),
        CheckConstraint(
            "status IN ('draft','requested','responded','under_review','accepted','rejected','closed')",
            name="ck_diligence_requests_status",
        ),
        CheckConstraint(
            "priority IN ('low','medium','high','critical')",
            name="ck_diligence_requests_priority",
        ),
        Index("ix_diligence_requests_deal_status_due", "deal_id", "status", "due_date"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workstream_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deal_workstreams.id", ondelete="SET NULL"), nullable=True
    )
    request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    owner_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    respondent_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DiligenceResponse(UUIDMixin, Base):
    __tablename__ = "diligence_responses"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_diligence_response_sequence"),
    )

    request_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("diligence_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    responded_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class DiligenceAttachment(UUIDMixin, Base):
    """Metadata for immutable object-store artifacts; binary bytes are stored externally."""

    __tablename__ = "diligence_attachments"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "source_hash", "version", name="uq_diligence_attachment_version"
        ),
    )

    request_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("diligence_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    response_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("diligence_responses.id", ondelete="SET NULL"), nullable=True
    )
    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uploaded_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class DealLedgerEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deal_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('thesis','issue','risk','decision')", name="ck_deal_ledger_type"
        ),
        CheckConstraint(
            "status IN ('open','validated','mitigated','accepted','rejected','resolved','superseded')",
            name="ck_deal_ledger_status",
        ),
        CheckConstraint(
            "severity IN ('low','medium','high','critical')", name="ck_deal_ledger_severity"
        ),
        Index("ix_deal_ledger_deal_type_status", "deal_id", "entry_type", "status"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    root_entry_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    supersedes_entry_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deal_ledger_entries.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    owner_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    related_artifact_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ICPacket(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ic_packets"
    __table_args__ = (
        UniqueConstraint("deal_id", "version", name="uq_ic_packet_version"),
        UniqueConstraint("deal_id", "content_hash", name="uq_ic_packet_content_hash"),
        CheckConstraint(
            "status IN ('draft','submitted','in_review','approved','conditional','deferred',"
            "'declined','superseded')",
            name="ck_ic_packets_status",
        ),
        Index("ix_ic_packets_deal_version", "deal_id", "version"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_packet_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("ic_packets.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    scenario_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    model_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_manifest: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    thesis_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    risk_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    decision_request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    readiness_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ready_for_submission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submitted_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ICComment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ic_comments"
    __table_args__ = (
        CheckConstraint("status IN ('open','resolved')", name="ck_ic_comments_status"),
        Index("ix_ic_comments_packet_status", "packet_id", "status"),
    )

    packet_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ic_packets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_comment_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("ic_comments.id", ondelete="SET NULL"), nullable=True
    )
    section_path: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    author_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ICDecision(UUIDMixin, Base):
    """Append-only IC vote/decision record. Packet status is only a current-state projection."""

    __tablename__ = "ic_decisions"
    __table_args__ = (
        UniqueConstraint("packet_id", "sequence", name="uq_ic_decision_sequence"),
        CheckConstraint(
            "decision IN ('approve','conditional','defer','decline')", name="ck_ic_decisions_value"
        ),
    )

    packet_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ic_packets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meeting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class ConditionToClose(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conditions_to_close"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','satisfied','waived')", name="ck_conditions_to_close_status"
        ),
        Index("ix_conditions_deal_status_due", "deal_id", "status", "due_date"),
    )

    deal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    packet_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ic_packets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ic_decisions.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ICPacketExport(UUIDMixin, Base):
    __tablename__ = "ic_packet_exports"
    __table_args__ = (
        CheckConstraint("format IN ('pdf','docx','xlsx','json')", name="ck_ic_exports_format"),
    )

    packet_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ic_packets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_by_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class WorkflowAuditEvent(UUIDMixin, Base):
    """Actor-aware, append-only event stream for workflow and IC mutations."""

    __tablename__ = "workflow_audit_events"
    __table_args__ = (
        Index("ix_workflow_audit_org_created", "organization_id", "created_at"),
        Index("ix_workflow_audit_deal_created", "deal_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deal_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deals.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    actor_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


def _reject_immutable_mutation(mapper, connection, target) -> None:  # pragma: no cover - hook
    del mapper, connection
    raise ValueError(f"{type(target).__name__} records are append-only")


for _immutable_model in (WorkflowAuditEvent, DealStageTransition, ICDecision):
    event.listen(_immutable_model, "before_update", _reject_immutable_mutation)
    event.listen(_immutable_model, "before_delete", _reject_immutable_mutation)

