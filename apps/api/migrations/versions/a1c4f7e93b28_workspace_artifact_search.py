"""workspace artifact full-text search (G34)

Revision ID: a1c4f7e93b28
Revises: d9f3b1a7e264
Create Date: 2026-07-15 18:00:00

G34 searches every workspace artifact (evidence, risk findings, diligence questions, memos,
filings, document chunks) at *query time* over the live tables — a workspace-scoped scan with
SQLite ``LIKE`` scoring / PostgreSQL ``to_tsvector`` + ``plainto_tsquery`` behind one interface in
``src.services.search_service``. Query-time search needs no separate index table and no sync
triggers, so it can never go stale and behaves identically on migrated and ``create_all``
databases (the interface + result shape are the parity contract; see ``tests/test_search.py``).

This revision therefore introduces no schema objects. It reserves the G34 slot in the linear
migration history (so later batches chain from a stable head) and documents the design decision.
It is idempotent by construction — a no-op on every engine — which keeps ``alembic upgrade`` and
``alembic check`` clean on a blank database and avoids adding untracked tables/indexes that would
otherwise diverge from the model metadata on the Postgres CI matrix (G36).
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "a1c4f7e93b28"
down_revision: str | Sequence[str] | None = "d9f3b1a7e264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema change — G34 search runs at query time over the live artifact tables."""


def downgrade() -> None:
    """No schema change to reverse."""
