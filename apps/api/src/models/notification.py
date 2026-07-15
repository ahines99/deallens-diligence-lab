"""In-app notifications projected from the append-only workflow audit outbox.

A notification is a read-model row derived from exactly one ``WorkflowAuditEvent``. The
``source_audit_event_id`` unique constraint makes the projection idempotent under an
at-least-once outbox consumer: re-running the sync can never duplicate a notification.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class Notification(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("source_audit_event_id", name="uq_notifications_audit_event"),
        Index("ix_notifications_org_created", "organization_id", "created_at"),
        Index("ix_notifications_org_read", "organization_id", "read_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_audit_event_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("workflow_audit_events.id", ondelete="CASCADE"),
        nullable=True,
    )
