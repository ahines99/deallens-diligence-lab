from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class DocumentChunk(UUIDMixin, TimestampMixin, Base):
    """A retrievable chunk of a filing / data-room document.

    BM25 scores these lexically; hybrid retrieval additionally uses ``embedding`` — a
    deterministic local feature-hashing vector (see ``embedding_service``) — fused with BM25
    via reciprocal-rank fusion. ``embedding_id`` records which embedding method produced the
    stored vector (the pluggable seam for a real/pgvector model later); both stay null until a
    chunk is embedded at ingest or by the backfill worker.
    """

    __tablename__ = "document_chunks"

    filing_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("filings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON list of floats (L2-normalized). Stored as JSON so SQLite test DBs work identically
    # to Postgres; a pgvector column is the production seam and the retrieval interface is the
    # same either way. Null when the chunk has not been embedded yet.
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
