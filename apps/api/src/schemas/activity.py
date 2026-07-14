"""Unified cross-plane activity timeline contracts."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ActivityItem(BaseModel):
    id: str
    source: str
    category: str
    event_type: str
    summary: str
    organization_id: str
    deal_id: str | None
    workspace_id: str | None
    actor_id: str | None
    entity_type: str
    entity_id: str
    detail: dict = Field(default_factory=dict)
    occurred_at: datetime


class ActivityTimeline(BaseModel):
    organization_id: str
    generated_at: datetime
    total: int
    items: list[ActivityItem]


__all__ = ["ActivityTimeline"]
