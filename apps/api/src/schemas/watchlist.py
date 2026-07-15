"""Watchlist contracts (G19)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from src.schemas.common import ORMModel


class WatchlistEntryCreate(BaseModel):
    """Add a company to the watchlist by ticker or CIK (at least one is required)."""

    ticker: str | None = Field(default=None, max_length=20)
    cik: str | None = Field(default=None, max_length=20)
    company_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _require_identifier(self) -> "WatchlistEntryCreate":
        if not (self.ticker or self.cik):
            raise ValueError("Provide a ticker or a CIK to watch.")
        return self


class WatchlistEntryOut(ORMModel):
    id: str
    organization_id: str
    ticker: str | None = None
    cik: str
    company_name: str
    last_seen_accession: str | None = None
    last_checked_at: datetime | None = None
    created_by: str | None = None
    active: bool
    created_at: datetime


class WatchlistRefreshResult(BaseModel):
    organization_id: str | None = None
    entries_checked: int
    new_filings: int
    events_emitted: int
    unavailable: int
