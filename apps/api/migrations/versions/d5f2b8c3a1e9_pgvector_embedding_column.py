"""pgvector embedding column (G83)

Revision ID: d5f2b8c3a1e9
Revises: c9e1a4b7d2f0
Create Date: 2026-07-18 09:00:00

Adds the PostgreSQL-only fast-path column ``document_chunks.embedding_vector`` (type
``vector``, deliberately undimensioned — the active embedding method decides the dimension,
and method isolation is enforced by ``embedding_id`` at query time). The JSON ``embedding``
column stays the source of truth on every backend; the vector column is a derived cache the
retrieval fast path maintains lazily (see ``retrieval_service``).

Dialect gating, explicitly:

* **SQLite** — complete no-op. SQLite keeps the Python cosine path; the schema there must stay
  byte-identical to model metadata (``create_all`` databases never grow this column, which is
  also why it is intentionally absent from the ORM model and hidden from autogenerate via
  ``env.py``'s ``include_object``).
* **PostgreSQL without the pgvector extension** — ``CREATE EXTENSION`` fails inside a
  SAVEPOINT, the failure is logged, and the migration completes WITHOUT the column. The app
  then serves every vector query from the Python path (probed and logged at runtime), so a
  managed Postgres without the extension is degraded-but-working, never broken.
* **PostgreSQL with pgvector** — extension ensured, column added, plus a partial btree index
  that accelerates the lazy backfill's "which rows still need casting" scan. NOTE: an ANN
  index (hnsw/ivfflat) is impossible on an undimensioned ``vector`` column — and unnecessary
  here: the fast path runs an exact workspace-scoped scan, which is precisely what the
  SQLite-parity contract requires.

Downgrade drops the index and column when present and leaves the extension installed
(extensions are deployment-owned; dropping one could break other databases' objects).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d5f2b8c3a1e9"
down_revision: str | Sequence[str] | None = "c9e1a4b7d2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_INDEX_NAME = "ix_document_chunks_vector_backfill"  # 34 chars, well under the 63-char cap


def _column_names(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("document_chunks")}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (and anything else): the JSON embedding + Python cosine path is the contract.
        return
    try:
        # SAVEPOINT so a failed CREATE EXTENSION (not installed on the server, insufficient
        # privileges on managed Postgres) cannot poison the migration transaction.
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except sa.exc.DBAPIError as exc:
        logger.warning(
            "pgvector extension unavailable (%s); skipping embedding_vector column — "
            "vector retrieval will use the Python path",
            exc,
        )
        return
    if "embedding_vector" not in _column_names(bind):
        op.execute(sa.text("ALTER TABLE document_chunks ADD COLUMN embedding_vector vector"))
    # Partial index for the lazy backfill scan (rows whose vector cache is still NULL).
    op.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} ON document_chunks (workspace_id) "
            "WHERE embedding_vector IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if "embedding_vector" in _column_names(bind):
        op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
        op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN embedding_vector"))
