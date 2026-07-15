"""Read-only tokenized share links for a frozen workspace snapshot (G44).

A share link lets someone with no account (e.g. an interviewer) walk a finished deal via a single
opaque URL. The secret is a ``dsh_<random>`` token shown exactly once at creation; only its SHA-256
digest is stored, mirroring the revocable-session and API-key designs. Resolution returns the bound
workspace and scope only while the link is unexpired and unrevoked. The public read surface exposes a
deliberately narrow, non-confidential snapshot (see ``share_link_service.build_snapshot``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class ShareLink(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "share_links"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_share_links_token_digest"),
        CheckConstraint("scope IN ('read_only')", name="ck_share_links_scope"),
        Index("ix_share_links_org_workspace", "organization_id", "workspace_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 hex digest of the ``dsh_`` plaintext token — the plaintext is never persisted.
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="read_only")
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
