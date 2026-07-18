"""In-app notification contracts."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: str
    organization_id: str
    actor_id: str | None = None
    recipient_user_id: str | None = None
    event_type: str
    entity_type: str
    entity_id: str
    title: str
    body: str
    read_at: datetime | None = None
    source_audit_event_id: str | None = None
    created_at: datetime


class UnreadCount(BaseModel):
    organization_id: str
    unread: int


class DigestEventGroup(BaseModel):
    """One event type's roll-up inside a digest window."""

    event_type: str
    count: int
    latest_title: str


class DigestInboxSla(BaseModel):
    """Compact G78 breach summary embedded in the digest (counts only)."""

    total_breaches: int
    breaches_by_plane: dict[str, int]


class DigestInbox(BaseModel):
    """Review-inbox aging summary for the digest's user."""

    total: int
    counts_by_plane: dict[str, int]
    oldest_age_hours: float | None = None
    sla: DigestInboxSla


class NotificationDigest(BaseModel):
    """G77 — per-user daily/weekly digest, computed on read (never a re-delivery)."""

    organization_id: str
    user_id: str | None = None
    window: str
    since: datetime
    until: datetime
    total: int
    by_event_type: list[DigestEventGroup]
    directed_count: int
    inbox: DigestInbox
