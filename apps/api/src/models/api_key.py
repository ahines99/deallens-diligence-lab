"""Scoped API keys for programmatic access (G38).

A key is an opaque ``dlk_<random>`` secret. Only its SHA-256 digest is stored; the plaintext
is shown exactly once at creation. Each key is bound to one organization, carries a JSON list
of granted scopes, and is subject to the same tenant guard as a human session — its scopes only
*narrow* what it may do, they never widen it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class ApiKey(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_digest", name="uq_api_key_digest"),
        Index("ix_api_keys_org_active", "organization_id", "revoked_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Non-secret visible identifier (e.g. ``dlk_ab12cd34``) shown in listings for recognition.
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # SHA-256 hex digest of the full plaintext secret — the plaintext is never persisted.
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
