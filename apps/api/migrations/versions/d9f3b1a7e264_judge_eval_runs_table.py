"""judge eval runs table (G05 faithfulness evals)

Revision ID: d9f3b1a7e264
Revises: c7e1a2f83b45
Create Date: 2026-07-15 16:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d9f3b1a7e264"
down_revision: str | Sequence[str] | None = "c7e1a2f83b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "judge_eval_runs" in existing:
        return
    op.create_table(
        "judge_eval_runs",
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("judge_name", sa.String(length=60), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=60), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("faithful", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("judge_eval_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_judge_eval_runs_workspace_id"), ["workspace_id"], unique=False
        )
        batch_op.create_index(
            "ix_judge_eval_runs_model_prompt", ["model_version", "prompt_version"], unique=False
        )
        batch_op.create_index(
            "ix_judge_eval_runs_workspace_created", ["workspace_id", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("judge_eval_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_judge_eval_runs_workspace_created")
        batch_op.drop_index("ix_judge_eval_runs_model_prompt")
        batch_op.drop_index(batch_op.f("ix_judge_eval_runs_workspace_id"))
    op.drop_table("judge_eval_runs")
