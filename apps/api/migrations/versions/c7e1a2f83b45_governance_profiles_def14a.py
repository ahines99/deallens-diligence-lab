"""def 14a governance profiles

Revision ID: c7e1a2f83b45
Revises: a3f1c9e27b04
Create Date: 2026-07-15 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c7e1a2f83b45"
down_revision: str | Sequence[str] | None = "a3f1c9e27b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "governance_profiles" in existing:
        return
    op.create_table(
        "governance_profiles",
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("def14a_accession", sa.String(length=30), nullable=True),
        sa.Column("filing_date", sa.String(length=20), nullable=True),
        sa.Column("exec_comp", sa.JSON(), nullable=False),
        sa.Column("red_flags", sa.JSON(), nullable=False),
        sa.Column("source_status", sa.String(length=20), nullable=False),
        sa.Column("raw_note", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_governance_profiles_workspace_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_governance_profiles_workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("governance_profiles")
