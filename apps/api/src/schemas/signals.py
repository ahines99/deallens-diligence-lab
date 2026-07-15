"""Response schemas for news signals and filing-watch automations.

Mirrors NewsSignals / NewsArticle / FilingWatch in apps/web/src/lib/types.ts. The workspace
refresh endpoint reuses WorkspaceOverview from src.schemas.workspace.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NewsArticle(BaseModel):
    title: str
    url: str
    domain: str
    seendate: str
    sourcecountry: str | None = None


class NewsSignals(BaseModel):
    workspace_id: str
    query: str
    articles: list[NewsArticle]
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    generated_at: datetime


class NewFiling(BaseModel):
    form: str
    date: str
    accession: str | None
    url: str | None


class FilingWatch(BaseModel):
    workspace_id: str
    last_ingested_date: str | None
    has_new: bool | None
    new_filings: list[NewFiling]
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    generated_at: datetime


# --- Insider-pattern analytics (Form 4) ------------------------------------
class InsiderCluster(BaseModel):
    direction: Literal["buy", "sell"]
    start: str
    end: str
    participants: int
    transactions: int
    total_shares: float | None
    total_value: float | None


class PlanSummary(BaseModel):
    planned: int
    discretionary: int
    unknown: int


class RoleBucket(BaseModel):
    buys: int
    sells: int


class RoleSplit(BaseModel):
    officer: RoleBucket
    director: RoleBucket
    ten_percent_owner: RoleBucket


class InsiderPatterns(BaseModel):
    workspace_id: str
    clusters: list[InsiderCluster]
    plan_summary: PlanSummary
    role_split: RoleSplit
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    generated_at: datetime
