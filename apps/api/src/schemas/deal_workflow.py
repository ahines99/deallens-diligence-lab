"""API contracts for tenant-aware deal execution and investment-committee workflows."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.common import ORMModel

DealStage = Literal[
    "sourcing",
    "screening",
    "initial_review",
    "diligence",
    "ic_review",
    "signing",
    "closed",
    "declined",
]
DealStatus = Literal["active", "on_hold", "closed", "declined"]
WorkflowStatus = Literal["not_started", "in_progress", "blocked", "complete", "waived"]
TaskStatus = Literal["not_started", "in_progress", "blocked", "complete", "cancelled"]
Priority = Literal["low", "medium", "high", "critical"]
GateStatus = Literal["pending", "satisfied", "waived"]
RequestStatus = Literal[
    "draft", "requested", "responded", "under_review", "accepted", "rejected", "closed"
]
LedgerType = Literal["thesis", "issue", "risk", "decision"]
LedgerStatus = Literal[
    "open", "validated", "mitigated", "accepted", "rejected", "resolved", "superseded"
]
ExcludeLedgerSuperseded = Literal[
    "open", "validated", "mitigated", "accepted", "rejected", "resolved"
]
TeamRole = Literal[
    "deal_lead",
    "investment_partner",
    "principal",
    "associate",
    "operating_partner",
    "finance",
    "legal",
    "advisor",
    "observer",
]
ICStatus = Literal[
    "draft", "submitted", "in_review", "approved", "conditional", "deferred", "declined", "superseded"
]
ICDecisionValue = Literal["approve", "conditional", "defer", "decline"]
ConditionStatus = Literal["open", "satisfied", "waived"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ActorContext(StrictModel):
    """Identity-provider-neutral request context populated by router headers.

    Authentication middleware can later construct the same contract from verified SSO claims.
    """

    actor_id: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    organization_id: str | None = Field(default=None, max_length=32)
    roles: tuple[str, ...] = ()
    request_id: str | None = Field(default=None, max_length=100)
    # True when the principal authenticated via the trusted-service internal token: its actor_id
    # is caller-chosen (X-Actor-ID), so four-eyes review planes must not accept it as a reviewer.
    via_trusted_service: bool = False


class OrganizationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=100)
    external_tenant_id: str | None = Field(default=None, max_length=200)
    identity_provider: dict[str, Any] | None = None

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("slug must contain lowercase letters, numbers, and single hyphens")
        return value


class OrganizationOut(ORMModel):
    id: str
    name: str
    slug: str
    external_tenant_id: str | None
    identity_provider: dict | None
    status: str
    created_at: datetime
    updated_at: datetime


class FundCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    vintage_year: int | None = Field(default=None, ge=1900, le=2200)
    base_currency: str = Field(default="USD", min_length=3, max_length=3)
    strategy: str = Field(default="buyout", min_length=1, max_length=80)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("base_currency must be an ISO-style alphabetic code")
        return value.upper()


class FundOut(ORMModel):
    id: str
    organization_id: str
    name: str
    vintage_year: int | None
    base_currency: str
    strategy: str
    status: str
    created_at: datetime
    updated_at: datetime


class DealCreate(StrictModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    target_company: str = Field(min_length=1, max_length=200)
    deal_type: str = Field(default="buyout", min_length=1, max_length=40)
    workspace_id: str | None = Field(default=None, max_length=32)
    owner_actor_id: str | None = Field(default=None, max_length=200)
    ic_date: date | None = None
    summary: str = Field(default="", max_length=20_000)
    seed_default_gates: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class DealPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_company: str | None = Field(default=None, min_length=1, max_length=200)
    workspace_id: str | None = Field(default=None, max_length=32)
    owner_actor_id: str | None = Field(default=None, max_length=200)
    ic_date: date | None = None
    summary: str | None = Field(default=None, max_length=20_000)
    status: Literal["active", "on_hold"] | None = None
    expected_version: int = Field(ge=1)


class DealOut(ORMModel):
    id: str
    organization_id: str
    fund_id: str
    workspace_id: str | None
    code: str
    name: str
    target_company: str
    deal_type: str
    stage: str
    status: str
    owner_actor_id: str | None
    ic_date: date | None
    summary: str
    version: int
    created_at: datetime
    updated_at: datetime


class StageGateCreate(StrictModel):
    stage: DealStage
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=240)
    required: bool = True


class StageGateResolve(StrictModel):
    status: Literal["satisfied", "waived"]
    resolution_note: str = Field(default="", max_length=10_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def waiver_requires_note(self):
        if self.status == "waived" and not self.resolution_note:
            raise ValueError("waived gates require a resolution_note")
        return self


class StageGateOut(ORMModel):
    id: str
    deal_id: str
    stage: str
    code: str
    label: str
    required: bool
    status: str
    evidence_refs: list
    resolution_note: str
    resolved_by_actor_id: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StageTransitionCreate(StrictModel):
    to_stage: DealStage
    rationale: str = Field(default="", max_length=20_000)


class StageTransitionOut(ORMModel):
    id: str
    deal_id: str
    sequence: int
    from_stage: str
    to_stage: str
    rationale: str
    actor_id: str | None
    created_at: datetime


class TeamMemberCreate(StrictModel):
    actor_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role: TeamRole


class TeamMemberOut(ORMModel):
    id: str
    deal_id: str
    actor_id: str
    display_name: str
    email: str | None
    role: str
    is_active: bool
    added_by_actor_id: str | None
    created_at: datetime
    updated_at: datetime


class WorkstreamCreate(StrictModel):
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    lead_actor_id: str | None = Field(default=None, max_length=200)
    due_date: date | None = None


class WorkstreamPatch(StrictModel):
    status: WorkflowStatus | None = None
    lead_actor_id: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    description: str | None = Field(default=None, max_length=20_000)


class WorkstreamOut(ORMModel):
    id: str
    deal_id: str
    slug: str
    label: str
    description: str
    status: str
    lead_actor_id: str | None
    due_date: date | None
    created_at: datetime
    updated_at: datetime


class MilestoneCreate(StrictModel):
    workstream_id: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=20_000)
    due_date: date | None = None
    owner_actor_id: str | None = Field(default=None, max_length=200)


class MilestonePatch(StrictModel):
    status: TaskStatus | None = None
    due_date: date | None = None
    owner_actor_id: str | None = Field(default=None, max_length=200)


class MilestoneOut(ORMModel):
    id: str
    deal_id: str
    workstream_id: str | None
    title: str
    description: str
    status: str
    due_date: date | None
    owner_actor_id: str | None
    completed_at: datetime | None
    completed_by_actor_id: str | None
    created_at: datetime
    updated_at: datetime


class TaskCreate(StrictModel):
    workstream_id: str | None = Field(default=None, max_length=32)
    milestone_id: str | None = Field(default=None, max_length=32)
    parent_task_id: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=20_000)
    priority: Priority = "medium"
    assignee_actor_id: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    dependency_task_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("dependency_task_ids")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("dependency_task_ids must be unique")
        return value


class TaskPatch(StrictModel):
    status: TaskStatus | None = None
    assignee_actor_id: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    priority: Priority | None = None
    blocked_reason: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def blocked_requires_reason(self):
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("blocked tasks require blocked_reason")
        return self


class TaskOut(ORMModel):
    id: str
    deal_id: str
    workstream_id: str | None
    milestone_id: str | None
    parent_task_id: str | None
    title: str
    description: str
    status: str
    priority: str
    assignee_actor_id: str | None
    due_date: date | None
    dependency_task_ids: list
    blocked_reason: str
    completed_at: datetime | None
    completed_by_actor_id: str | None
    created_at: datetime
    updated_at: datetime


class DiligenceRequestCreate(StrictModel):
    workstream_id: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=240)
    question: str = Field(min_length=1, max_length=50_000)
    rationale: str = Field(default="", max_length=20_000)
    priority: Priority = "medium"
    owner_actor_id: str | None = Field(default=None, max_length=200)
    respondent_actor_id: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    send_now: bool = False


class DiligenceRequestOut(ORMModel):
    id: str
    deal_id: str
    workstream_id: str | None
    request_number: int
    title: str
    question: str
    rationale: str
    status: str
    priority: str
    owner_actor_id: str | None
    respondent_actor_id: str | None
    due_date: date | None
    requested_at: datetime | None
    last_response_at: datetime | None
    accepted_at: datetime | None
    accepted_by_actor_id: str | None
    review_note: str
    created_at: datetime
    updated_at: datetime


class DiligenceResponseCreate(StrictModel):
    response_text: str = Field(min_length=1, max_length=100_000)


class DiligenceResponseOut(ORMModel):
    id: str
    request_id: str
    sequence: int
    response_text: str
    responded_by_actor_id: str | None
    submitted_at: datetime


class DiligenceAttachmentCreate(StrictModel):
    response_id: str | None = Field(default=None, max_length=32)
    artifact_id: str | None = Field(default=None, max_length=64)
    filename: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=2_000)
    source_hash: str = Field(min_length=64, max_length=64)
    media_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(ge=0, le=10_000_000_000)
    version: int = Field(default=1, ge=1)

    @field_validator("source_hash")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("source_hash must be a SHA-256 hex digest")
        return value


class DiligenceAttachmentOut(ORMModel):
    id: str
    request_id: str
    response_id: str | None
    artifact_id: str | None
    filename: str
    object_key: str
    source_hash: str
    media_type: str
    size_bytes: int
    version: int
    uploaded_by_actor_id: str | None
    created_at: datetime


class DiligenceReview(StrictModel):
    action: Literal["accept", "reject"]
    note: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def rejection_requires_note(self):
        if self.action == "reject" and not self.note:
            raise ValueError("rejected responses require a review note")
        return self


class LedgerEntryCreate(StrictModel):
    entry_type: LedgerType
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=100_000)
    status: ExcludeLedgerSuperseded = "open"
    severity: Priority = "medium"
    owner_actor_id: str | None = Field(default=None, max_length=200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=1_000)
    related_artifact_ids: list[str] = Field(default_factory=list, max_length=1_000)


class LedgerEntryRevision(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, min_length=1, max_length=100_000)
    status: ExcludeLedgerSuperseded | None = None
    severity: Priority | None = None
    owner_actor_id: str | None = Field(default=None, max_length=200)
    evidence_refs: list[str] | None = Field(default=None, max_length=1_000)
    related_artifact_ids: list[str] | None = Field(default=None, max_length=1_000)


class LedgerEntryOut(ORMModel):
    id: str
    deal_id: str
    root_entry_id: str | None
    supersedes_entry_id: str | None
    version: int
    entry_type: str
    title: str
    description: str
    status: str
    severity: str
    owner_actor_id: str | None
    evidence_refs: list
    related_artifact_ids: list
    created_by_actor_id: str | None
    created_at: datetime
    updated_at: datetime


class ICPacketCreate(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    assembly_mode: Literal["governed"] = "governed"
    case_version_ids: list[str] = Field(default_factory=list, max_length=20)
    approved_claim_ids: list[str] = Field(default_factory=list, max_length=2_000)
    workspace_evidence_refs: list[str] = Field(default_factory=list, max_length=2_000)
    legacy_reason: str | None = Field(default=None, max_length=2_000)
    scenario_snapshot: dict[str, Any] | None = None
    model_snapshot: dict[str, Any] | None = None
    evidence_manifest: list[dict[str, Any]] | None = None
    thesis_snapshot: list[dict[str, Any]] | None = None
    risk_snapshot: list[dict[str, Any]] | None = None
    decision_request: dict[str, Any]
    previous_packet_id: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_assembly_contract(self):
        reference_lists = (
            self.case_version_ids,
            self.approved_claim_ids,
            self.workspace_evidence_refs,
        )
        if any(len(values) != len(set(values)) for values in reference_lists):
            raise ValueError("IC packet source references must be unique")
        legacy_snapshots = (
            self.scenario_snapshot,
            self.model_snapshot,
            self.evidence_manifest,
            self.thesis_snapshot,
            self.risk_snapshot,
        )
        if not self.case_version_ids:
            raise ValueError("governed IC packet assembly requires case_version_ids")
        if any(value is not None for value in legacy_snapshots):
            raise ValueError("governed assembly does not accept client-owned snapshots")
        if self.legacy_reason:
            raise ValueError("legacy client snapshots are no longer accepted")
        return self


class ICPacketOut(ORMModel):
    id: str
    deal_id: str
    version: int
    previous_packet_id: str | None
    title: str
    status: str
    scenario_snapshot: dict
    model_snapshot: dict
    evidence_manifest: list
    thesis_snapshot: list
    risk_snapshot: list
    decision_request: dict
    readiness_snapshot: dict
    ready_for_submission: bool
    content_hash: str
    created_by_actor_id: str | None
    submitted_by_actor_id: str | None
    submitted_at: datetime | None
    frozen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReadinessCheck(StrictModel):
    code: str
    passed: bool
    message: str
    blocking_count: int = 0
    entity_ids: list[str] = Field(default_factory=list)


class ReadinessResult(StrictModel):
    packet_id: str
    checked_at: datetime
    ready: bool
    checks: list[ReadinessCheck]


class ICCommentCreate(StrictModel):
    parent_comment_id: str | None = Field(default=None, max_length=32)
    section_path: str = Field(default="", max_length=240)
    body: str = Field(min_length=1, max_length=100_000)
    blocking: bool = False


class ICCommentResolve(StrictModel):
    resolution: str = Field(min_length=1, max_length=100_000)


class ICCommentOut(ORMModel):
    id: str
    packet_id: str
    parent_comment_id: str | None
    section_path: str
    body: str
    blocking: bool
    status: str
    author_actor_id: str | None
    resolution: str
    resolved_by_actor_id: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConditionCreate(StrictModel):
    description: str = Field(min_length=1, max_length=100_000)
    owner_actor_id: str | None = Field(default=None, max_length=200)
    due_date: date | None = None


class ICDecisionCreate(StrictModel):
    decision: ICDecisionValue
    rationale: str = Field(min_length=1, max_length=100_000)
    meeting_at: datetime | None = None
    conditions: list[ConditionCreate] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def conditional_requires_conditions(self):
        if self.decision == "conditional" and not self.conditions:
            raise ValueError("conditional approval requires at least one condition")
        if self.decision != "conditional" and self.conditions:
            raise ValueError("conditions are only valid with a conditional decision")
        return self


class ICDecisionOut(ORMModel):
    id: str
    packet_id: str
    sequence: int
    decision: str
    rationale: str
    decided_by_actor_id: str | None
    meeting_at: datetime | None
    is_final: bool
    decided_at: datetime


class ConditionPatch(StrictModel):
    status: Literal["satisfied", "waived"]
    resolution_note: str = Field(default="", max_length=100_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def waiver_requires_note(self):
        if self.status == "waived" and not self.resolution_note:
            raise ValueError("waived conditions require a resolution_note")
        return self


class ConditionOut(ORMModel):
    id: str
    deal_id: str
    packet_id: str
    decision_id: str
    description: str
    owner_actor_id: str | None
    due_date: date | None
    status: str
    evidence_refs: list
    resolution_note: str
    resolved_by_actor_id: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ICDecisionResult(StrictModel):
    decision: ICDecisionOut
    conditions: list[ConditionOut]


class SnapshotDiff(StrictModel):
    path: str
    change: Literal["added", "removed", "changed"]
    before: Any = None
    after: Any = None


class PacketDiffResult(StrictModel):
    from_packet_id: str
    to_packet_id: str
    from_version: int
    to_version: int
    changes: list[SnapshotDiff]


class ExportRequest(StrictModel):
    format: Literal["pdf", "docx", "xlsx", "json"]


class ExportManifestOut(ORMModel):
    id: str
    packet_id: str
    format: str
    manifest: dict
    manifest_hash: str
    requested_by_actor_id: str | None
    created_at: datetime


class ExportVerificationResult(StrictModel):
    export_id: str
    packet_id: str
    verified_at: datetime
    valid: bool
    manifest_hash: str
    recomputed_manifest_hash: str
    checks: list[ReadinessCheck]


class WorkflowAuditOut(ORMModel):
    id: str
    organization_id: str
    deal_id: str | None
    actor_id: str | None
    actor_display_name: str | None
    action: str
    entity_type: str
    entity_id: str
    detail: dict
    request_id: str | None
    created_at: datetime
