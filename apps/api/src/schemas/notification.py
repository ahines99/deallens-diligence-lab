"""In-app notification contracts."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: str
    organization_id: str
    actor_id: str | None = None
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
