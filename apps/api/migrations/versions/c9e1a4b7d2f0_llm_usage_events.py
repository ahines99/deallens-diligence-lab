"""llm usage telemetry events

Revision ID: c9e1a4b7d2f0
Revises: a7d4e2f9c318
Create Date: 2026-07-18 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c9e1a4b7d2f0"
down_revision: str | Sequence[str] | None = "a7d4e2f9c318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "llm_usage_events" in existing:
        return
    op.create_table(
        "llm_usage_events",
        sa.Column("organization_id", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("llm_usage_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_llm_usage_events_organization_id"),
            ["organization_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_usage_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_llm_usage_events_organization_id"))
    op.drop_table("llm_usage_events")
