"""redaction decision CHECK backstop + llm usage time index

Revision ID: a4e8c1f6d9b3
Revises: f8b4d0e5c3a2
Create Date: 2026-07-18 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a4e8c1f6d9b3"
down_revision: str | Sequence[str] | None = "f8b4d0e5c3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_redaction_proposal_decided_fields"
_CHECK_CONDITION = (
    "status = 'proposed' OR (decided_by_actor_id IS NOT NULL AND decided_at IS NOT "
    "NULL AND decided_by_actor_id <> proposed_by_actor_id)"
)
_USAGE_INDEX = "ix_llm_usage_events_created_at"


def _has_check_constraint(bind, table: str, name: str) -> bool:
    try:
        constraints = sa.inspect(bind).get_check_constraints(table)
    except NotImplementedError:  # pragma: no cover - dialect without CHECK reflection
        return False
    return any(constraint.get("name") == name for constraint in constraints)


def _has_index(bind, table: str, name: str) -> bool:
    return any(index["name"] == name for index in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    # Runtime-created databases (SCHEMA_MANAGEMENT=create_all) already carry both objects.
    bind = op.get_bind()
    if not _has_check_constraint(bind, "redaction_proposals", _CHECK_NAME):
        with op.batch_alter_table("redaction_proposals", schema=None) as batch_op:
            batch_op.create_check_constraint(_CHECK_NAME, _CHECK_CONDITION)
    if not _has_index(bind, "llm_usage_events", _USAGE_INDEX):
        with op.batch_alter_table("llm_usage_events", schema=None) as batch_op:
            batch_op.create_index(_USAGE_INDEX, ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "llm_usage_events", _USAGE_INDEX):
        with op.batch_alter_table("llm_usage_events", schema=None) as batch_op:
            batch_op.drop_index(_USAGE_INDEX)
    if _has_check_constraint(bind, "redaction_proposals", _CHECK_NAME):
        with op.batch_alter_table("redaction_proposals", schema=None) as batch_op:
            batch_op.drop_constraint(_CHECK_NAME, type_="check")
