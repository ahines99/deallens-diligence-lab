"""Response schemas for news signals and filing-watch automations.

Mirrors NewsSignals / NewsArticle / FilingWatch in apps/web/src/lib/types.ts. The workspace
refresh endpoint reuses WorkspaceOverview from src.schemas.workspace.
"""
from __future__ import annotations

from datetime import datetime

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
    generated_at: datetime


class NewFiling(BaseModel):
    form: str
    date: str
    accession: str | None
    url: str | None


class FilingWatch(BaseModel):
    workspace_id: str
    last_ingested_date: str | None
    has_new: bool
    new_filings: list[NewFiling]
    generated_at: datetime
