"""durable background job queue

Revision ID: b2d8e6a41c57
Revises: f4a9c7d21b3e
Create Date: 2026-07-14 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b2d8e6a41c57"
down_revision: str | Sequence[str] | None = "f4a9c7d21b3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "background_jobs" in existing:
        return
    op.create_table(
        "background_jobs",
        sa.Column("job_type", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=120), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','dead')",
            name="ck_background_job_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("background_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_background_jobs_job_type"), ["job_type"], unique=False)
        batch_op.create_index("ix_background_jobs_status_next", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("background_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_background_jobs_status_next")
        batch_op.drop_index(batch_op.f("ix_background_jobs_job_type"))
    op.drop_table("background_jobs")
