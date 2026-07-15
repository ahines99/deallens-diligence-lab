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
