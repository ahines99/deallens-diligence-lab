"""Append-only underwriting case versions and review decisions.

Case inputs and calculated outputs are stored together as immutable snapshots.  Human
workflow state is deliberately represented by separate append-only decisions so an approval
never rewrites the economics that were reviewed.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class UnderwritingCaseVersion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "underwriting_case_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "case_key", "version", name="uq_underwriting_case_version"
        ),
        CheckConstraint("case_key IN ('base', 'upside', 'downside')", name="ck_case_key"),
        CheckConstraint("version > 0", name="ck_case_version_positive"),
        Index("ix_underwriting_case_workspace_key", "workspace_id", "case_key"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_key: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("underwriting_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    assumptions: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    approved_claim_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approved_claim_manifest: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    claim_manifest_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class UnderwritingCaseDecision(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "underwriting_case_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('submitted', 'approved', 'rejected', 'superseded')",
            name="ck_underwriting_decision",
        ),
        Index("ix_underwriting_decision_version_created", "case_version_id", "created_at"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_version_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("underwriting_case_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")


def _reject_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are append-only")


def _validate_decision_actor(_mapper, connection, target: UnderwritingCaseDecision) -> None:
    """Require a named actor and two-person control for an approval or rejection."""
    actor = (target.actor or "").strip()
    if not actor:
        raise ValueError("Underwriting decisions require an authenticated actor")
    if target.decision not in {"approved", "rejected"}:
        return
    table = UnderwritingCaseDecision.__table__
    submitter = connection.execute(
        select(table.c.actor)
        .where(
            table.c.case_version_id == target.case_version_id,
            table.c.decision == "submitted",
        )
        .order_by(table.c.created_at.desc(), table.c.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if submitter is None:
        raise ValueError("Approval or rejection requires a prior submitted decision")
    if submitter == actor:
        raise ValueError("The underwriting submitter cannot approve or reject the same case")


@event.listens_for(Session, "do_orm_execute")
def _reject_bulk_case_mutation(execute_state) -> None:  # pragma: no cover - exercised by tests
    """Mapper hooks do not see bulk SQL, so close that immutability bypass as well."""
    if not (execute_state.is_update or execute_state.is_delete):
        return
    table = getattr(execute_state.statement, "table", None)
    if table is not None and table.name in {
        UnderwritingCaseVersion.__tablename__,
        UnderwritingCaseDecision.__tablename__,
    }:
        raise ValueError(f"{table.name} records are append-only")


# These guards make the immutability contract explicit even for callers bypassing the service.
event.listen(UnderwritingCaseVersion, "before_update", _reject_mutation)
event.listen(UnderwritingCaseVersion, "before_delete", _reject_mutation)
event.listen(UnderwritingCaseDecision, "before_update", _reject_mutation)
event.listen(UnderwritingCaseDecision, "before_delete", _reject_mutation)
event.listen(UnderwritingCaseDecision, "before_insert", _validate_decision_actor)
