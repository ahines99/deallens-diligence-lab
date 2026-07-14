"""identity, workspace governance, evidence uniqueness, and webhook replay

Revision ID: 9c4f32a7b8e1
Revises: 0fcfabe85d5e
Create Date: 2026-07-13 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9c4f32a7b8e1"
down_revision: str | Sequence[str] | None = "0fcfabe85d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _assert_unique_evidence_refs() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT workspace_id, ref, COUNT(*) AS duplicate_count
            FROM evidence
            GROUP BY workspace_id, ref
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate:
        raise RuntimeError(
            "Cannot enforce evidence reference uniqueness: workspace "
            f"{duplicate['workspace_id']} has {duplicate['duplicate_count']} rows for "
            f"{duplicate['ref']}. Reconcile the ambiguous evidence records before migrating."
        )


def _backfill_workspace_organizations() -> None:
    """Carry tenant ownership from the existing one-to-one deal/workspace link."""
    op.get_bind().execute(
        sa.text(
            """
            UPDATE workspaces
            SET organization_id = (
                SELECT deals.organization_id
                FROM deals
                WHERE deals.workspace_id = workspaces.id
            )
            WHERE organization_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM deals
                  WHERE deals.workspace_id = workspaces.id
              )
            """
        )
    )


def _replace_foreign_key(
    table_name: str,
    column_name: str,
    referred_table: str,
    *,
    ondelete: str,
) -> None:
    inspector = sa.inspect(op.get_bind())
    foreign_key = next(
        (
            item
            for item in inspector.get_foreign_keys(table_name)
            if item["constrained_columns"] == [column_name]
            and item["referred_table"] == referred_table
        ),
        None,
    )
    if foreign_key is None:
        raise RuntimeError(f"Expected foreign key {table_name}.{column_name} was not found")
    existing_name = foreign_key["name"] or f"fk_{table_name}_{column_name}_{referred_table}"
    target_name = f"fk_{table_name}_{column_name}_{referred_table}"
    with op.batch_alter_table(
        table_name,
        schema=None,
        naming_convention=_FK_NAMING,
    ) as batch_op:
        batch_op.drop_constraint(existing_name, type_="foreignkey")
        batch_op.create_foreign_key(
            target_name,
            referred_table,
            [column_name],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    identity_tables = {"users", "organization_memberships", "auth_sessions"}
    workspace_columns = {item["name"] for item in inspector.get_columns("workspaces")}
    evidence_uniques = {
        item["name"] for item in inspector.get_unique_constraints("evidence")
    }
    webhook_columns = {
        item["name"] for item in inspector.get_columns("webhook_deliveries")
    }
    current_create_all_schema = (
        identity_tables <= tables
        and {"organization_id", "data_classification", "external_llm_allowed"}
        <= workspace_columns
        and "uq_evidence_workspace_ref" in evidence_uniques
        and "replayed_from_delivery_id" in webhook_columns
    )
    if current_create_all_schema:
        # A legacy runtime may already have created the exact current metadata. Alembic still needs
        # to stamp the revisions, but no destructive reconstruction is necessary.
        _assert_unique_evidence_refs()
        _backfill_workspace_organizations()
        return
    if tables & identity_tables:
        raise RuntimeError(
            "Identity schema is partially migrated. Restore all of users, "
            "organization_memberships, and auth_sessions together before retrying."
        )

    workspace_fks = inspector.get_foreign_keys("workspaces")
    workspace_indexes = {item["name"] for item in inspector.get_indexes("workspaces")}
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        if "organization_id" not in workspace_columns:
            batch_op.add_column(sa.Column("organization_id", sa.String(length=32), nullable=True))
        if "data_classification" not in workspace_columns:
            batch_op.add_column(
                sa.Column(
                    "data_classification",
                    sa.String(length=30),
                    nullable=False,
                    server_default="confidential",
                )
            )
        if "external_llm_allowed" not in workspace_columns:
            batch_op.add_column(
                sa.Column(
                    "external_llm_allowed",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if not any(item["constrained_columns"] == ["organization_id"] for item in workspace_fks):
            batch_op.create_foreign_key(
                "fk_workspaces_organization_id_organizations",
                "organizations",
                ["organization_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if "ix_workspaces_organization_id" not in workspace_indexes:
            batch_op.create_index(
                batch_op.f("ix_workspaces_organization_id"), ["organization_id"], unique=False
            )

    _backfill_workspace_organizations()

    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_users_email_normalized"), ["email_normalized"], unique=False
        )

    op.create_table(
        "organization_memberships",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','admin','member','viewer')", name="ck_membership_role"
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended')", name="ck_membership_status"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )
    with op.batch_alter_table("organization_memberships", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_organization_memberships_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_organization_memberships_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            "ix_memberships_org_role", ["organization_id", "role"], unique=False
        )

    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("membership_id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["organization_memberships.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_auth_session_token_digest"),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_auth_sessions_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_auth_sessions_membership_id"), ["membership_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_auth_sessions_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_auth_sessions_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            "ix_auth_sessions_user_active",
            ["user_id", "revoked_at", "expires_at"],
            unique=False,
        )

    _assert_unique_evidence_refs()
    evidence_uniques = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_unique_constraints("evidence")
    }
    if "uq_evidence_workspace_ref" not in evidence_uniques:
        with op.batch_alter_table("evidence", schema=None) as batch_op:
            batch_op.create_unique_constraint(
                "uq_evidence_workspace_ref", ["workspace_id", "ref"]
            )

    # Immutable case versions and decisions must survive workspace cleanup attempts.
    _replace_foreign_key(
        "underwriting_case_versions", "workspace_id", "workspaces", ondelete="RESTRICT"
    )
    _replace_foreign_key(
        "underwriting_case_decisions", "workspace_id", "workspaces", ondelete="RESTRICT"
    )
    _replace_foreign_key(
        "underwriting_case_decisions",
        "case_version_id",
        "underwriting_case_versions",
        ondelete="RESTRICT",
    )

    webhook_columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("webhook_deliveries")
    }
    if "replayed_from_delivery_id" not in webhook_columns:
        with op.batch_alter_table("webhook_deliveries", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("replayed_from_delivery_id", sa.String(length=32), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_webhook_deliveries_replayed_from_delivery_id",
                "webhook_deliveries",
                ["replayed_from_delivery_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                batch_op.f("ix_webhook_deliveries_replayed_from_delivery_id"),
                ["replayed_from_delivery_id"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("webhook_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_webhook_deliveries_replayed_from_delivery_id"))
        batch_op.drop_constraint(
            "fk_webhook_deliveries_replayed_from_delivery_id", type_="foreignkey"
        )
        batch_op.drop_column("replayed_from_delivery_id")

    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.drop_constraint("uq_evidence_workspace_ref", type_="unique")

    _replace_foreign_key(
        "underwriting_case_decisions",
        "case_version_id",
        "underwriting_case_versions",
        ondelete="CASCADE",
    )
    _replace_foreign_key(
        "underwriting_case_decisions", "workspace_id", "workspaces", ondelete="CASCADE"
    )
    _replace_foreign_key(
        "underwriting_case_versions", "workspace_id", "workspaces", ondelete="CASCADE"
    )

    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_auth_sessions_user_active")
        batch_op.drop_index(batch_op.f("ix_auth_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_organization_id"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_membership_id"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_expires_at"))
    op.drop_table("auth_sessions")

    with op.batch_alter_table("organization_memberships", schema=None) as batch_op:
        batch_op.drop_index("ix_memberships_org_role")
        batch_op.drop_index(batch_op.f("ix_organization_memberships_user_id"))
        batch_op.drop_index(batch_op.f("ix_organization_memberships_organization_id"))
    op.drop_table("organization_memberships")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_email_normalized"))
    op.drop_table("users")

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workspaces_organization_id"))
        batch_op.drop_constraint(
            "fk_workspaces_organization_id_organizations", type_="foreignkey"
        )
        batch_op.drop_column("external_llm_allowed")
        batch_op.drop_column("data_classification")
        batch_op.drop_column("organization_id")
