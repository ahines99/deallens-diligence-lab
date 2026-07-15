"""scoped api keys for programmatic access

Revision ID: c3e9a1f7b52d
Revises: a1c4f7e93b28
Create Date: 2026-07-15 13:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3e9a1f7b52d"
down_revision: str | Sequence[str] | None = "a1c4f7e93b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have the table.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "api_keys" in existing:
        return
    op.create_table(
        "api_keys",
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_api_keys_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_api_keys_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_digest", name="uq_api_key_digest"),
    )
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_api_keys_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_api_keys_key_prefix"), ["key_prefix"], unique=False)
        batch_op.create_index(
            "ix_api_keys_org_active", ["organization_id", "revoked_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_index("ix_api_keys_org_active")
        batch_op.drop_index(batch_op.f("ix_api_keys_key_prefix"))
        batch_op.drop_index(batch_op.f("ix_api_keys_organization_id"))
    op.drop_table("api_keys")
