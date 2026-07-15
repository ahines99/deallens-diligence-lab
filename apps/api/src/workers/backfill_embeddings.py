"""Backfill chunk embeddings for workspaces ingested before hybrid retrieval existed.

New chunks are embedded at ingest (see ``sec_ingestion_service``). This one-shot worker fills
the embedding for any ``DocumentChunk`` that still has a null vector, so existing workspaces get
hybrid retrieval without re-ingesting from EDGAR. It is idempotent: only null-embedding rows are
touched, so a second run embeds nothing and creates no duplicates.

Run it with ``python -m src.workers.backfill_embeddings`` (optionally ``--workspace <id>``).
"""
from __future__ import annotations

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.session import SessionLocal, prepare_schema
from src.models import DocumentChunk
from src.services import embedding_service

logger = logging.getLogger("deallens.backfill_embeddings")


def backfill_embeddings(session: Session, workspace_id: str | None = None) -> dict:
    """Embed every chunk with a null embedding (optionally scoped to one workspace).

    Returns a count summary. Commits only when work was done, so re-running against an already
    embedded corpus is a cheap read-only no-op.
    """
    stmt = select(DocumentChunk).where(DocumentChunk.embedding.is_(None))
    if workspace_id:
        stmt = stmt.where(DocumentChunk.workspace_id == workspace_id)
    chunks = list(session.scalars(stmt))
    for chunk in chunks:
        embedding_service.embed_chunk(chunk)
    if chunks:
        session.commit()
    return {"embedded": len(chunks), "method": embedding_service.EMBED_METHOD}


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
