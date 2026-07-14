"""enforce the workspace target back-reference

Revision ID: e81b6d2f04aa
Revises: d73a0e4c91f2
Create Date: 2026-07-14 02:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e81b6d2f04aa"
down_revision: str | Sequence[str] | None = "d73a0e4c91f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_workspaces_target_id_targets"
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _has_target_fk() -> bool:
    return any(
        foreign_key.get("constrained_columns") == ["target_id"]
        and foreign_key.get("referred_table") == "targets"
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys("workspaces")
    )


def upgrade() -> None:
    if _has_target_fk():
        return
    orphan = op.get_bind().execute(
        sa.text(
            """
            SELECT target_id
            FROM workspaces
            WHERE target_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM targets WHERE targets.id = workspaces.target_id)
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if orphan:
        raise RuntimeError(
            "Cannot enforce workspace target integrity while an orphaned target_id exists: "
            f"{orphan}"
        )
    with op.batch_alter_table("workspaces", naming_convention=_NAMING) as batch_op:
        batch_op.create_foreign_key(
            _FK_NAME,
            "targets",
            ["target_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if not _has_target_fk():
        return
    with op.batch_alter_table("workspaces", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
