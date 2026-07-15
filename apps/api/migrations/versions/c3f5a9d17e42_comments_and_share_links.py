"""comment threads and read-only share links (G41, G44)

Creates the ``comments`` table (general @mention threads on any governed artifact) and the
``share_links`` table (read-only tokenized workspace snapshots), and adds the
``notifications.recipient_user_id`` column that lets a mention project into a directed notification.

Revision ID: c3f5a9d17e42
Revises: e2a7c4f18d63
Create Date: 2026-07-15 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3f5a9d17e42"
down_revision: str | Sequence[str] | None = "e2a7c4f18d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) may already have these objects.
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if "comments" not in existing_tables:
        op.create_table(
            "comments",
            sa.Column("organization_id", sa.String(length=32), nullable=False),
            sa.Column("author_user_id", sa.String(length=200), nullable=True),
            sa.Column("author_display_name", sa.String(length=200), nullable=True),
            sa.Column("entity_type", sa.String(length=40), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("parent_comment_id", sa.String(length=32), nullable=True),
            sa.Column("mentions", sa.JSON(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_by_user_id", sa.String(length=200), nullable=True),
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "entity_type IN ('risk','qoe_adjustment','memo','ic_packet','workspace')",
                name="ck_comments_entity_type",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["organizations.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["parent_comment_id"], ["comments.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("comments", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_comments_organization_id"), ["organization_id"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_comments_author_user_id"), ["author_user_id"], unique=False
            )
            batch_op.create_index(
                "ix_comments_entity",
                ["organization_id", "entity_type", "entity_id"],
                unique=False,
            )
            batch_op.create_index(
                "ix_comments_parent", ["parent_comment_id"], unique=False
            )

    if "share_links" not in existing_tables:
        op.create_table(
            "share_links",
            sa.Column("organization_id", sa.String(length=32), nullable=False),
            sa.Column("workspace_id", sa.String(length=32), nullable=False),
            sa.Column("token_digest", sa.String(length=64), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=200), nullable=True),
            sa.Column("scope", sa.String(length=20), nullable=False),
            sa.Column("label", sa.String(length=200), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("scope IN ('read_only')", name="ck_share_links_scope"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["organizations.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_digest", name="uq_share_links_token_digest"),
        )
        with op.batch_alter_table("share_links", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_share_links_organization_id"), ["organization_id"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_share_links_workspace_id"), ["workspace_id"], unique=False
            )
            batch_op.create_index(
                "ix_share_links_org_workspace",
                ["organization_id", "workspace_id"],
                unique=False,
            )

    notification_columns = {
        column["name"] for column in inspector.get_columns("notifications")
    }
    if "recipient_user_id" not in notification_columns:
        with op.batch_alter_table("notifications", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("recipient_user_id", sa.String(length=200), nullable=True)
            )
            batch_op.create_index(
                batch_op.f("ix_notifications_recipient_user_id"),
                ["recipient_user_id"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_recipient_user_id"))
        batch_op.drop_column("recipient_user_id")

    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.drop_index("ix_share_links_org_workspace")
        batch_op.drop_index(batch_op.f("ix_share_links_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_share_links_organization_id"))
    op.drop_table("share_links")

    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.drop_index("ix_comments_parent")
        batch_op.drop_index("ix_comments_entity")
        batch_op.drop_index(batch_op.f("ix_comments_author_user_id"))
        batch_op.drop_index(batch_op.f("ix_comments_organization_id"))
    op.drop_table("comments")
