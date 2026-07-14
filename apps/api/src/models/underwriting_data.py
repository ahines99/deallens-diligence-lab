"""Append-only underwriting data, provenance, and quality-of-earnings models.

The existing application stores a convenient current-state summary on ``Target``.  The
tables in this module are deliberately more rigorous: imported values retain their
period, unit, source locator, and immutable source version so an IC artifact can be
reproduced later.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin, now_utc


MONEY = Numeric(28, 6)


class SourceSnapshot(UUIDMixin, Base):
    """A sealed version of an input source.

    A new upload creates a new row and points at the version it supersedes.  Snapshot
    rows are never updated; processing status is therefore known before the row is
    persisted (``ready``, ``partial``, or ``failed``).
    """

    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_type",
            "source_name",
            "version",
            name="uq_source_snapshot_stream_version",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("targets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("source_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str | None] = mapped_column(String(260), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class AnalysisRun(UUIDMixin, Base):
    """An append-only, terminal analysis execution record."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "run_type", "version", name="uq_analysis_run_type_version"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_snapshot_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    input_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    code_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class ArtifactVersion(UUIDMixin, Base):
    """A sealed memo, model, export, or other analysis artifact version."""

    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "artifact_type", "version", name="uq_artifact_type_version"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("artifact_versions.id", ondelete="SET NULL"), nullable=True
    )
    analysis_run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    source_snapshot_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class AccountMapping(UUIDMixin, Base):
    """Versioned mapping from a source account label to the canonical chart."""

    __tablename__ = "account_mappings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_type",
            "raw_account_normalized",
            "version",
            name="uq_account_mapping_version",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(60), nullable=False, default="management")
    raw_account: Mapped[str] = mapped_column(String(240), nullable=False)
    raw_account_normalized: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    canonical_account: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(String(30), nullable=False)
    sign_multiplier: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="approved")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("account_mappings.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class CanonicalFinancialFact(UUIDMixin, Base):
    """One normalized financial value tied to an exact source row and period."""

    __tablename__ = "canonical_financial_facts"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "row_hash", name="uq_fact_snapshot_row_hash"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_mapping_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("account_mappings.id", ondelete="SET NULL"), nullable=True
    )
    statement: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    raw_account: Mapped[str] = mapped_column(String(240), nullable=False)
    raw_account_normalized: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    canonical_account: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    mapping_state: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    scale_factor: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=1)
    value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False, default="currency")
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class FinancialReconciliation(UUIDMixin, Base):
    """Balance-sheet reconciliation result for one imported period."""

    __tablename__ = "financial_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id", "period_end", name="uq_reconciliation_snapshot_period"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    assets: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    liabilities_and_equity: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    tolerance: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class FinancialImportException(UUIDMixin, TimestampMixin, Base):
    """An explicit exception; missing data is never represented as a clean zero."""

    __tablename__ = "financial_import_exceptions"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("canonical_financial_facts.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QoEAdjustment(UUIDMixin, TimestampMixin, Base):
    """A signed adjustment applied at one layer of the EBITDA bridge."""

    __tablename__ = "qoe_adjustments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_key", name="uq_qoe_adjustment_dedupe"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_snapshot_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("source_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bridge_layer: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="other")
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_run_rate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_cash: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    owner: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    evidence_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_update(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable; create a new version")


for _immutable_model in (SourceSnapshot, AnalysisRun, ArtifactVersion):
    event.listen(_immutable_model, "before_update", _reject_update)
    event.listen(_immutable_model, "before_delete", _reject_update)


@event.listens_for(Session, "do_orm_execute")
def _reject_bulk_immutable_mutation(execute_state) -> None:  # pragma: no cover - integration tested
    if not (execute_state.is_update or execute_state.is_delete):
        return
    table = getattr(execute_state.statement, "table", None)
    if table is not None and table.name in {
        SourceSnapshot.__tablename__,
        AnalysisRun.__tablename__,
        ArtifactVersion.__tablename__,
    }:
        raise ValueError(f"{table.name} records are immutable; create a new version")


__all__ = [
    "AccountMapping",
    "AnalysisRun",
    "ArtifactVersion",
    "CanonicalFinancialFact",
    "FinancialImportException",
    "FinancialReconciliation",
    "QoEAdjustment",
    "SourceSnapshot",
]
