"""fine-grained membership permission grants

Revision ID: a7d4e2f9c318
Revises: c3f5a9d17e42
Create Date: 2026-07-15 15:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a7d4e2f9c318"
down_revision: str | Sequence[str] | None = "c3f5a9d17e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "membership_permissions" in existing:
        return
    op.create_table(
        "membership_permissions",
        sa.Column("membership_id", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=60), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["organization_memberships.id"],
            name="fk_membership_permissions_membership_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("membership_id", "capability", name="uq_membership_permission"),
    )
    with op.batch_alter_table("membership_permissions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_membership_permissions_membership_id"),
            ["membership_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("membership_permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_membership_permissions_membership_id"))
    op.drop_table("membership_permissions")
