"""four-eyes data-room redaction proposals

Revision ID: f8b4d0e5c3a2
Revises: e7a3c9d4b2f1
Create Date: 2026-07-18 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f8b4d0e5c3a2"
down_revision: str | Sequence[str] | None = "e7a3c9d4b2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "redaction_proposals" in existing:
        return
    op.create_table(
        "redaction_proposals",
        sa.Column("deal_id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("logical_document_id", sa.String(length=32), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("spans", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.Column("proposed_by_actor_id", sa.String(length=200), nullable=False),
        sa.Column("decided_by_actor_id", sa.String(length=200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_document_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed','approved','rejected')",
            name="ck_redaction_proposal_status",
        ),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["data_room_documents.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["redacted_document_id"], ["data_room_documents.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("redaction_proposals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_redaction_proposals_deal_id"), ["deal_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_redaction_proposals_document_id"), ["document_id"], unique=False
        )
        batch_op.create_index(
            "ix_redaction_proposals_deal_status", ["deal_id", "status"], unique=False
        )
        batch_op.create_index(
            "ix_redaction_proposals_deal_logical",
            ["deal_id", "logical_document_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("redaction_proposals", schema=None) as batch_op:
        batch_op.drop_index("ix_redaction_proposals_deal_logical")
        batch_op.drop_index("ix_redaction_proposals_deal_status")
        batch_op.drop_index(batch_op.f("ix_redaction_proposals_document_id"))
        batch_op.drop_index(batch_op.f("ix_redaction_proposals_deal_id"))
    op.drop_table("redaction_proposals")
