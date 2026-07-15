"""in-app notifications table

Revision ID: 7286ee9488f6
Revises: b2d8e6a41c57
Create Date: 2026-07-15 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "7286ee9488f6"
down_revision: str | Sequence[str] | None = "b2d8e6a41c57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "notifications" in existing:
        return
    op.create_table(
        "notifications",
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_audit_event_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_notifications_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_audit_event_id"],
            ["workflow_audit_events.id"],
            name="fk_notifications_audit_event_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_audit_event_id", name="uq_notifications_audit_event"),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notifications_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notifications_event_type"), ["event_type"], unique=False
        )
        batch_op.create_index(
            "ix_notifications_org_created", ["organization_id", "created_at"], unique=False
        )
        batch_op.create_index(
            "ix_notifications_org_read", ["organization_id", "read_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index("ix_notifications_org_read")
        batch_op.drop_index("ix_notifications_org_created")
        batch_op.drop_index(batch_op.f("ix_notifications_event_type"))
        batch_op.drop_index(batch_op.f("ix_notifications_organization_id"))
    op.drop_table("notifications")
