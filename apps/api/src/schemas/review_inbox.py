"""Response contract for the G42 "My reviews" inbox."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ReviewItem(BaseModel):
    plane: str
    id: str
    title: str
    deal_or_workspace: str
    created_at: datetime
    url_hint: str


class ReviewInboxOut(BaseModel):
    organization_id: str
    actor_id: str
    items: list[ReviewItem]
    counts_by_plane: dict[str, int]
    total: int


class ReviewAgingBreach(BaseModel):
    """One item whose age exceeds its plane's SLA threshold."""

    id: str
    title: str
    age_hours: float
    sla_hours: float


class ReviewAgingPlane(BaseModel):
    count: int
    oldest_age_hours: float | None = None
    sla_hours: float
    breaches: list[ReviewAgingBreach]


class ReviewAgingOut(BaseModel):
    """G78 — aging report over the same four planes (and exclusions) as the review inbox."""

    organization_id: str
    actor_id: str
    as_of: datetime
    sla_hours: dict[str, float]
    planes: dict[str, ReviewAgingPlane]
    total: int
    total_breaches: int
