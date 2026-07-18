"""Append-only view events for share links (G76).

One row per successful public snapshot read through ``GET /api/shared/{token}``. Rows are
append-only by nature: nothing in the codebase updates or deletes them — they are only
inserted (``share_link_service.record_view``, best-effort: a failed insert never breaks the
public read) and aggregated for the owning organization
(``share_link_service.get_share_link_analytics``). Invalid, revoked, or expired tokens never
record a view.

Privacy — deliberately coarse "where". Only the request's ``User-Agent`` (truncated) and the
transport-level client host are stored, mirroring exactly what ``AuthSession`` already keeps
for logged-in sessions (``src.models.identity``). No cookies, no canvas/device fingerprinting,
no geo lookup: a share-link viewer has no account and consented to nothing beyond opening the
link, so the analytics answer "was this link used, when, and roughly from what" — never "who
is this person". The public route never exposes these events; they are owner/org-scoped only.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, UUIDMixin, now_utc


class ShareLinkView(UUIDMixin, Base):
    """One successful public read of a share link's snapshot."""

    __tablename__ = "share_link_views"
    __table_args__ = (
        # Covers both the count aggregate and the newest-first "recent" scan per link.
        Index("ix_share_link_views_link_viewed", "share_link_id", "viewed_at"),
    )

    share_link_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    # Coarse context only — see the module docstring for the privacy rationale.
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    client_host: Mapped[str | None] = mapped_column(String(64), nullable=True)
