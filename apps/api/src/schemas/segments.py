"""Schemas for XBRL segment-level revenue from dimensional facts (G12)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SegmentPeriod(BaseModel):
    period_end: str
    revenue: float


class SegmentSeries(BaseModel):
    segment_name: str
    member: str
    source_concept: str
    periods: list[SegmentPeriod]


class SegmentRevenue(BaseModel):
    workspace_id: str
    target_name: str
    # available = members reconcile to consolidated; partial = members don't fully reconcile;
    # unavailable = no dimensional segment detail (companyfacts consolidated-only, or not yet
    # extracted for a legacy workspace — see source_note).
    source_status: Literal["available", "partial", "unavailable"]
    source_note: str | None
    # The XBRL reporting axis the members came from (e.g. us-gaap:StatementBusinessSegmentsAxis).
    axis: str | None
    segments: list[SegmentSeries]
    generated_at: datetime
