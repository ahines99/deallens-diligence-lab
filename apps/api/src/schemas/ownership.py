"""Pydantic schemas for institutional ownership (13F) and activist-stake (13D/13G) signals.

Both feeds read live, keyless SEC EDGAR data and preserve the explicit source_status discipline
(available / partial / unavailable) rather than emitting false-clean empties. Mirrors the
conventions in `apps/api/src/schemas/feeds.py`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


# --- 13F institutional ownership -------------------------------------------
class Holding(BaseModel):
    issuer: str
    cusip: str | None = None
    title: str | None = None
    value: float | None = None
    shares: float | None = None


class Concentration(BaseModel):
    hhi: float | None
    top5_share: float | None
    holder_count: int
    total_value: float | None


class InstitutionalOwnership(BaseModel):
    workspace_id: str
    # `manager_portfolio` — target itself files 13F; we report its holdings' concentration.
    # `not_applicable` — target is not a 13F filer; keyless reverse holder-lookup is unavailable.
    scope: Literal["manager_portfolio", "not_applicable"]
    manager_name: str | None = None
    period_of_report: str | None = None
    holdings: list[Holding]
    concentration: Concentration
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    note: str
    generated_at: datetime


# --- 13D/13G activist-stake timeline ---------------------------------------
class ActivistStakeEvent(BaseModel):
    type: Literal["13D", "13G"]
    form: str
    filer: str | None = None
    filing_date: str
    accession: str | None = None
    url: str | None = None
    percent_owned: float | None = None
    is_activist: bool
    is_amendment: bool


class ActivistStakes(BaseModel):
    workspace_id: str
    events: list[ActivistStakeEvent]
    source_status: Literal["available", "partial", "unavailable"]
    source_error: str | None = None
    note: str
    generated_at: datetime
