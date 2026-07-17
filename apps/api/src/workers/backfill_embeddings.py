"""Backfill chunk embeddings: fill null vectors and refresh stale-method vectors.

New chunks are embedded at ingest (see ``sec_ingestion_service``). This one-shot worker embeds
any ``DocumentChunk`` whose vector is null OR whose ``embedding_id`` tag differs from the ACTIVE
embedding method (G55: after an embedding backend/model change, old vectors live in a different
space and retrieval ignores them until re-embedded). It is idempotent: rows already carrying the
active method are untouched, so a second run embeds nothing and creates no duplicates.

Run it with ``python -m src.workers.backfill_embeddings`` (optionally ``--workspace <id>``).
"""
from __future__ import annotations

import argparse
import logging

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.db.session import SessionLocal, prepare_schema
from src.models import DocumentChunk
from src.services import embedding_service

logger = logging.getLogger("deallens.backfill_embeddings")


def backfill_embeddings(session: Session, workspace_id: str | None = None) -> dict:
    """Embed every null-vector or stale-method chunk (optionally scoped to one workspace).

    Returns a count summary. Commits only when work was done, so re-running against an already
    embedded corpus is a cheap read-only no-op.
    """
    active = embedding_service.active_method()
    stmt = select(DocumentChunk).where(
        or_(
            DocumentChunk.embedding.is_(None),
            DocumentChunk.embedding_id.is_(None),
            DocumentChunk.embedding_id != active,
        )
    )
    if workspace_id:
        stmt = stmt.where(DocumentChunk.workspace_id == workspace_id)
    chunks = list(session.scalars(stmt))
    for chunk in chunks:
        embedding_service.embed_chunk(chunk)
    if chunks:
        session.commit()
    return {"embedded": len(chunks), "method": active}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill DealLens chunk embeddings")
    parser.add_argument(
        "--workspace", default=None, help="Only backfill chunks in this workspace id"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    prepare_schema()
    with SessionLocal() as session:
        result = backfill_embeddings(session, workspace_id=args.workspace)
    logger.info("Embedding backfill complete: %s", result)


if __name__ == "__main__":
    main()
