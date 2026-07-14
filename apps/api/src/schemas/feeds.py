"""Pydantic schemas for SEC event/insider/theme feeds. Mirror `apps/web/src/lib/types.ts`."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


# --- Events (8-K timeline) -------------------------------------------------
class EventItem(BaseModel):
    code: str
    label: str


class FilingEvent(BaseModel):
    date: str
    form: str
    items: list[EventItem]
    accession: str | None
    url: str | None
    significant: bool


class EventTimeline(BaseModel):
    workspace_id: str
    events: list[FilingEvent]
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    generated_at: datetime


# --- Insider activity (Form 4) ---------------------------------------------
class InsiderTx(BaseModel):
    date: str
    insider: str
    role: str
    type: Literal["buy", "sell", "other"]
    shares: float | None
    price: float | None
    value: float | None
    url: str | None


class InsiderSummary(BaseModel):
    buys: int | None
    sells: int | None
    net_shares: float | None
    window_days: int


class InsiderActivity(BaseModel):
    workspace_id: str
    summary: InsiderSummary
    transactions: list[InsiderTx]
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    generated_at: datetime


# --- Theme scan (EDGAR full-text search) -----------------------------------
class ThemeHitRef(BaseModel):
    form: str
    date: str
    url: str | None


class ThemeHit(BaseModel):
    theme: str
    label: str
    count: int | None
    hits: list[ThemeHitRef]


class ThemeScan(BaseModel):
    workspace_id: str
    themes: list[ThemeHit]
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    generated_at: datetime
