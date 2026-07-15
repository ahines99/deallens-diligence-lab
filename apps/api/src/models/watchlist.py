"""G19 — watchlists with scheduled refresh.

A ``WatchlistEntry`` tracks one company (per organization) for new SEC filings. The scheduled
refresh worker reads EDGAR submissions for each active entry and, for every filing newer than
``last_seen_accession``, emits a ``WorkflowAuditEvent`` through the existing outbox (which the
notification and webhook fan-outs consume). ``last_seen_accession`` is the dedup cursor: a filing
already recorded there is never re-emitted.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class WatchlistEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "watchlist_entries"
    __table_args__ = (
        # One entry per company per tenant. CIK is the EDGAR key the refresh queries against.
        UniqueConstraint("organization_id", "cik", name="uq_watchlist_org_cik"),
        Index("ix_watchlist_org_active", "organization_id", "active"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cik: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Dedup cursor: the newest accession already observed/emitted. NULL until the first refresh
    # establishes a baseline (so an existing filing backlog never floods notifications).
    last_seen_accession: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
