"""API contracts for private-company underwriting data and QoE workflows."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.common import ORMModel


SourceKind = Literal["financials", "document", "market_data", "filing", "user_input"]
SnapshotStatus = Literal["ready", "partial", "failed"]
StatementType = Literal["income_statement", "balance_sheet", "cash_flow", "kpi"]
PeriodType = Literal["month", "quarter", "year", "ytd", "ltm", "instant"]
MappingStatus = Literal["draft", "approved", "rejected"]
BridgeLayer = Literal["management", "sponsor", "covenant"]
AdjustmentDecision = Literal["approve", "reject"]
TerminalRunStatus = Literal["succeeded", "failed", "cancelled"]

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9_]{1,119}$")


def _clean_hash(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not _HASH_RE.fullmatch(cleaned):
        raise ValueError("hash must be a 64-character lowercase SHA-256 hex digest")
    return cleaned


def _clean_currency(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", cleaned):
        raise ValueError("currency must be a three-letter ISO-style code")
    return cleaned


def _clean_canonical_account(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip().lower()
    if not _CANONICAL_ACCOUNT_RE.fullmatch(cleaned):
        raise ValueError("canonical_account must be lowercase snake_case")
    return cleaned


class PrivateTargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sector: str = Field(default="", max_length=120)
    description: str = ""
    fiscal_year_end: str | None = Field(default=None, max_length=20)

    @field_validator("name", "sector", "description", "fiscal_year_end", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SourceSnapshotCreate(BaseModel):
    source_kind: SourceKind
    source_type: str = Field(min_length=1, max_length=60)
    source_name: str = Field(min_length=1, max_length=240)
    filename: str | None = Field(default=None, max_length=260)
    content_type: str | None = Field(default=None, max_length=120)
    storage_uri: str | None = None
    input_hash: str | None = None
    content_hash: str
    byte_size: int | None = Field(default=None, ge=0)
    record_count: int = Field(default=0, ge=0)
    status: SnapshotStatus = "ready"
    source_metadata: dict | None = None
    created_by: str = Field(default="system", min_length=1, max_length=120)

    _validate_input_hash = field_validator("input_hash")(_clean_hash)
    _validate_content_hash = field_validator("content_hash")(_clean_hash)

    @field_validator(
        "source_type", "source_name", "filename", "content_type", "created_by", mode="before"
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SourceSnapshotOut(ORMModel):
    id: str
    workspace_id: str
    target_id: str | None
    source_kind: str
    source_type: str
    source_name: str
    version: int
    supersedes_id: str | None
    filename: str | None
    content_type: str | None
    storage_uri: str | None
    input_hash: str
    content_hash: str
    byte_size: int | None
    record_count: int
    status: str
    source_metadata: dict | None
    created_by: str
    created_at: datetime
    sealed_at: datetime


class AccountMappingCreate(BaseModel):
    source_type: str = Field(default="management", min_length=1, max_length=60)
    raw_account: str = Field(min_length=1, max_length=240)
    canonical_account: str
    statement: StatementType
    sign_multiplier: Decimal = Decimal("1")
    status: MappingStatus = "approved"
    created_by: str = Field(default="system", min_length=1, max_length=120)
    approved_by: str | None = Field(default=None, max_length=120)

    @field_validator("canonical_account")
    @classmethod
    def validate_required_canonical_account(cls, value: str) -> str:
        cleaned = _clean_canonical_account(value)
        if cleaned is None:
            raise ValueError("canonical_account is required")
        return cleaned

    @field_validator("sign_multiplier")
    @classmethod
    def reject_zero_multiplier(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("sign_multiplier cannot be zero")
        return value

    @field_validator("source_type", "raw_account", "created_by", "approved_by", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class AccountMappingOut(ORMModel):
    id: str
    workspace_id: str
    source_type: str
    raw_account: str
    raw_account_normalized: str
    canonical_account: str
    statement: str
    sign_multiplier: Decimal
    status: str
    version: int
    supersedes_id: str | None
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


class NormalizedFinancialRow(BaseModel):
    raw_account: str = Field(min_length=1, max_length=240)
    canonical_account: str | None = None
    statement: StatementType
    period_start: date | None = None
    period_end: date
    period_type: PeriodType
    value: Decimal
    scale: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="currency", min_length=1, max_length=30)
    currency: str | None = "USD"
    source_sheet: str | None = Field(default=None, max_length=160)
    source_row: int | None = Field(default=None, ge=1)
    source_locator: str | None = None
    provenance: dict | None = None

    _validate_canonical_account = field_validator("canonical_account")(
        _clean_canonical_account
    )
    _validate_currency = field_validator("currency")(_clean_currency)

    @field_validator(
        "raw_account", "unit", "source_sheet", "source_locator", mode="before"
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_period_and_unit(self):
        if self.period_start and self.period_start > self.period_end:
            raise ValueError("period_start cannot be after period_end")
        if self.period_type == "instant" and self.period_start is not None:
            raise ValueError("instant facts cannot have period_start")
        if self.unit == "currency" and self.currency is None:
            raise ValueError("currency facts require currency")
        return self


class FinancialImportCreate(BaseModel):
    source_name: str = Field(min_length=1, max_length=240)
    source_type: str = Field(default="management_financials", min_length=1, max_length=60)
    filename: str | None = Field(default=None, max_length=260)
    content_type: str | None = Field(default=None, max_length=120)
    rows: list[NormalizedFinancialRow] = Field(min_length=1, max_length=100_000)
    source_metadata: dict | None = None
    reconciliation_tolerance_bps: Decimal = Field(
        default=Decimal("50"), ge=0, le=Decimal("1000")
    )
    created_by: str = Field(default="system", min_length=1, max_length=120)

    @field_validator(
        "source_name", "source_type", "filename", "content_type", "created_by", mode="before"
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class CanonicalFinancialFactOut(ORMModel):
    id: str
    workspace_id: str
    target_id: str
    source_snapshot_id: str
    account_mapping_id: str | None
    statement: str
    raw_account: str
    raw_account_normalized: str
    canonical_account: str | None
    mapping_state: str
    period_start: date | None
    period_end: date
    period_type: str
    raw_value: Decimal
    scale_factor: Decimal
    value: Decimal
    unit: str
    currency: str | None
    source_sheet: str | None
    source_row: int | None
    source_locator: str
    provenance: dict | None
    row_hash: str
    created_at: datetime


class FinancialReconciliationOut(ORMModel):
    id: str
    workspace_id: str
    source_snapshot_id: str
    period_end: date
    assets: Decimal | None
    liabilities_and_equity: Decimal | None
    difference: Decimal | None
    tolerance: Decimal | None
    status: str
    details: dict | None
    created_at: datetime


class FinancialImportExceptionOut(ORMModel):
    id: str
    workspace_id: str
    source_snapshot_id: str
    fact_id: str | None
    code: str
    severity: str
    state: str
    message: str
    details: dict | None
    resolved_by: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FinancialImportExceptionResolution(BaseModel):
    resolved_by: str = Field(min_length=1, max_length=120)


class FinancialImportResult(BaseModel):
    snapshot: SourceSnapshotOut
    row_count: int
    mapped_count: int
    unmapped_count: int
    open_exception_count: int
    reconciliations: list[FinancialReconciliationOut]


class FinancialImportPreviewException(BaseModel):
    code: str
    severity: str
    message: str
    details: dict | None = None
    row_number: int | None = None


class FinancialImportPreviewReconciliation(BaseModel):
    period_end: date
    assets: Decimal | None
    liabilities_and_equity: Decimal | None
    difference: Decimal | None
    tolerance: Decimal | None
    status: str
    details: dict | None


class FinancialImportPreview(BaseModel):
    will_write: Literal[False] = False
    proposed_source_version: int
    supersedes_source_id: str | None
    input_hash: str
    normalized_content_hash: str
    row_count: int
    mapped_count: int
    unmapped_count: int
    projected_status: SnapshotStatus
    open_exception_count: int
    exceptions: list[FinancialImportPreviewException]
    reconciliations: list[FinancialImportPreviewReconciliation]


class QoEAdjustmentCreate(BaseModel):
    period_start: date | None = None
    period_end: date
    bridge_layer: BridgeLayer
    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    category: str = Field(default="other", min_length=1, max_length=80)
    amount: Decimal
    currency: str = "USD"
    is_recurring: bool = False
    is_run_rate: bool = False
    is_cash: bool = True
    owner: str = Field(default="", max_length=120)
    evidence_ref: str | None = Field(default=None, max_length=40)
    source_snapshot_id: str | None = None
    source_locator: str | None = None
    created_by: str = Field(default="system", min_length=1, max_length=120)

    _validate_currency = field_validator("currency")(_clean_currency)

    @field_validator("amount")
    @classmethod
    def reject_zero_amount(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("amount cannot be zero")
        return value

    @field_validator(
        "title",
        "description",
        "category",
        "owner",
        "evidence_ref",
        "source_locator",
        "created_by",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_period_and_evidence(self):
        if self.period_start and self.period_start > self.period_end:
            raise ValueError("period_start cannot be after period_end")
        if self.source_snapshot_id and not self.source_locator:
            raise ValueError("source_locator is required when source_snapshot_id is supplied")
        return self


class QoEAdjustmentDecision(BaseModel):
    decision: AdjustmentDecision
    decided_by: str = Field(min_length=1, max_length=120)
    note: str = ""


class QoEAdjustmentOut(ORMModel):
    id: str
    workspace_id: str
    target_id: str
    source_snapshot_id: str | None
    period_start: date | None
    period_end: date
    bridge_layer: str
    title: str
    description: str
    category: str
    amount: Decimal
    currency: str
    is_recurring: bool
    is_run_rate: bool
    is_cash: bool
    owner: str
    evidence_ref: str | None
    source_locator: str | None
    status: str
    created_by: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str
    created_at: datetime
    updated_at: datetime


class QoEBridgeOut(BaseModel):
    workspace_id: str
    target_id: str
    period_end: date | None
    currency: str | None
    status: Literal["ready", "incomplete"]
    reported_ebitda: Decimal | None
    management_adjustments: Decimal
    management_ebitda: Decimal | None
    sponsor_adjustments: Decimal
    sponsor_ebitda: Decimal | None
    covenant_adjustments: Decimal
    covenant_ebitda: Decimal | None
    included_adjustment_ids: list[str]
    excluded_adjustment_count: int
    source_snapshot_id: str | None
    source_locator: str | None
    derivation: dict | None
    warnings: list[str]


class AnalysisRunCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    run_type: str = Field(min_length=1, max_length=80)
    status: TerminalRunStatus = "succeeded"
    source_snapshot_ids: list[str] = Field(default_factory=list)
    input_manifest: dict = Field(default_factory=dict)
    output_summary: dict = Field(default_factory=dict)
    input_hash: str | None = None
    content_hash: str | None = None
    model_version: str | None = Field(default=None, max_length=120)
    prompt_version: str | None = Field(default=None, max_length=120)
    code_version: str | None = Field(default=None, max_length=120)
    error_message: str | None = None
    created_by: str = Field(default="system", min_length=1, max_length=120)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    _validate_input_hash = field_validator("input_hash")(_clean_hash)
    _validate_content_hash = field_validator("content_hash")(_clean_hash)

    @field_validator(
        "run_type",
        "model_version",
        "prompt_version",
        "code_version",
        "created_by",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_terminal_result(self):
        if self.status == "failed" and not self.error_message:
            raise ValueError("failed analysis runs require error_message")
        if self.started_at and self.completed_at and self.started_at > self.completed_at:
            raise ValueError("started_at cannot be after completed_at")
        return self


class AnalysisRunOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    workspace_id: str
    run_type: str
    version: int
    supersedes_id: str | None
    status: str
    input_hash: str
    content_hash: str
    source_snapshot_ids: list | None
    input_manifest: dict | None
    output_summary: dict | None
    model_version: str | None
    prompt_version: str | None
    code_version: str | None
    error_message: str | None
    created_by: str
    started_at: datetime
    completed_at: datetime
    created_at: datetime


class ArtifactVersionCreate(BaseModel):
    artifact_type: str = Field(min_length=1, max_length=80)
    analysis_run_id: str | None = None
    source_snapshot_ids: list[str] = Field(default_factory=list)
    input_manifest: dict = Field(default_factory=dict)
    input_hash: str | None = None
    content_hash: str | None = None
    content_json: dict | list | None = None
    content_text: str | None = None
    file_uri: str | None = None
    artifact_metadata: dict | None = None
    created_by: str = Field(default="system", min_length=1, max_length=120)

    _validate_input_hash = field_validator("input_hash")(_clean_hash)
    _validate_content_hash = field_validator("content_hash")(_clean_hash)

    @field_validator("artifact_type", "file_uri", "created_by", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_content(self):
        if self.content_json is None and self.content_text is None and self.file_uri is None:
            raise ValueError("an artifact requires content_json, content_text, or file_uri")
        if (
            self.file_uri is not None
            and self.content_json is None
            and self.content_text is None
            and self.content_hash is None
        ):
            raise ValueError("file-backed artifacts require the file content_hash")
        return self


class ArtifactVersionOut(ORMModel):
    id: str
    workspace_id: str
    artifact_type: str
    version: int
    supersedes_id: str | None
    analysis_run_id: str | None
    source_snapshot_ids: list | None
    input_hash: str
    content_hash: str
    content_json: dict | list | None
    content_text: str | None
    file_uri: str | None
    artifact_metadata: dict | None
    created_by: str
    created_at: datetime


__all__ = [
    "AccountMappingCreate",
    "AccountMappingOut",
    "AnalysisRunCreate",
    "AnalysisRunOut",
    "ArtifactVersionCreate",
    "ArtifactVersionOut",
    "CanonicalFinancialFactOut",
    "FinancialImportCreate",
    "FinancialImportExceptionOut",
    "FinancialImportExceptionResolution",
    "FinancialImportResult",
    "FinancialImportPreview",
    "FinancialReconciliationOut",
    "NormalizedFinancialRow",
    "PrivateTargetCreate",
    "QoEAdjustmentCreate",
    "QoEAdjustmentDecision",
    "QoEAdjustmentOut",
    "QoEBridgeOut",
    "SourceSnapshotCreate",
    "SourceSnapshotOut",
]
