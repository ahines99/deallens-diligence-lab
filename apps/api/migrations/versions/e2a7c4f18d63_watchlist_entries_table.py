"""watchlist entries table

Revision ID: e2a7c4f18d63
Revises: c3e9a1f7b52d
Create Date: 2026-07-15 13:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e2a7c4f18d63"
down_revision: str | Sequence[str] | None = "c3e9a1f7b52d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "watchlist_entries" in existing:
        return
    op.create_table(
        "watchlist_entries",
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=True),
        sa.Column("cik", sa.String(length=20), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("last_seen_accession", sa.String(length=40), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_watchlist_entries_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "cik", name="uq_watchlist_org_cik"),
    )
    with op.batch_alter_table("watchlist_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_watchlist_entries_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            "ix_watchlist_org_active", ["organization_id", "active"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("watchlist_entries", schema=None) as batch_op:
        batch_op.drop_index("ix_watchlist_org_active")
        batch_op.drop_index(batch_op.f("ix_watchlist_entries_organization_id"))
    op.drop_table("watchlist_entries")
