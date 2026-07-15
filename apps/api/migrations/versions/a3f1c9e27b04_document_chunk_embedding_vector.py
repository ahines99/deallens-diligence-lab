"""document chunk embedding vector

Revision ID: a3f1c9e27b04
Revises: 7286ee9488f6
Create Date: 2026-07-15 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a3f1c9e27b04"
down_revision: str | Sequence[str] | None = "7286ee9488f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) already have this column from
    # the model, so adding it must be a no-op there — keep the migration idempotent.
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("document_chunks")
    }
    if "embedding" not in existing:
        with op.batch_alter_table("document_chunks", schema=None) as batch_op:
            batch_op.add_column(sa.Column("embedding", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.drop_column("embedding")
