"""share link view events (G76)

Revision ID: e7a3c9d4b2f1
Revises: d5f2b8c3a1e9
Create Date: 2026-07-18 09:00:00

Creates the append-only ``share_link_views`` table: one row per successful public snapshot
read through ``GET /api/shared/{token}``. Context is deliberately coarse (truncated
``user_agent`` + transport-level ``client_host``, mirroring what ``auth_sessions`` already
stores) — see ``src.models.share_link_view`` for the privacy rationale. Rows are only ever
inserted and aggregated, never updated or deleted; the owning share link's CASCADE removes
them with the link.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7a3c9d4b2f1"
down_revision: str | Sequence[str] | None = "d5f2b8c3a1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    inspector = sa.inspect(op.get_bind())
    if "share_link_views" in set(inspector.get_table_names()):
        return

    op.create_table(
        "share_link_views",
        sa.Column("share_link_id", sa.String(length=32), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(length=200), nullable=True),
        sa.Column("client_host", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["share_link_id"], ["share_links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("share_link_views", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_share_link_views_share_link_id"), ["share_link_id"], unique=False
        )
        # Covers the count aggregate and the newest-first "recent" scan. 31 chars, under the
        # 63-char PostgreSQL identifier cap.
        batch_op.create_index(
            "ix_share_link_views_link_viewed", ["share_link_id", "viewed_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("share_link_views", schema=None) as batch_op:
        batch_op.drop_index("ix_share_link_views_link_viewed")
        batch_op.drop_index(batch_op.f("ix_share_link_views_share_link_id"))
    op.drop_table("share_link_views")
