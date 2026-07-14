"""workspace live-ingestion build status

Revision ID: f4a9c7d21b3e
Revises: e81b6d2f04aa
Create Date: 2026-07-14 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f4a9c7d21b3e"
down_revision: str | Sequence[str] | None = "e81b6d2f04aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("build_status", "build_step", "build_error", "build_ticker")


def upgrade() -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("workspaces")
    }
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        if "build_status" not in existing:
            batch_op.add_column(
                sa.Column(
                    "build_status",
                    sa.String(length=20),
                    nullable=False,
                    server_default="ready",
                )
            )
        if "build_step" not in existing:
            batch_op.add_column(sa.Column("build_step", sa.String(length=40), nullable=True))
        if "build_error" not in existing:
            batch_op.add_column(sa.Column("build_error", sa.Text(), nullable=True))
        if "build_ticker" not in existing:
            batch_op.add_column(sa.Column("build_ticker", sa.String(length=20), nullable=True))

    # Existing workspaces were fully built synchronously at creation time; carry the
    # target ticker over so their refresh/retry paths have the same inputs as new rows.
    op.get_bind().execute(
        sa.text(
            """
            UPDATE workspaces
            SET build_ticker = (
                SELECT targets.ticker FROM targets WHERE targets.workspace_id = workspaces.id
            )
            WHERE build_ticker IS NULL
              AND EXISTS (
                  SELECT 1 FROM targets
                  WHERE targets.workspace_id = workspaces.id AND targets.ticker IS NOT NULL
              )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        for column_name in reversed(_COLUMNS):
            batch_op.drop_column(column_name)
