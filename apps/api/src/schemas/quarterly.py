"""Schemas for 10-Q quarterly financials + trailing-twelve-month derivation (G11)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class QuarterRow(BaseModel):
    start: str | None
    end: str
    fy: str | None
    fp: str | None
    form: str | None
    revenue: float | None
    gross_profit: float | None
    operating_income: float | None
    net_income: float | None
    # metric -> derivation label (e.g. "fy_minus_q123" when Q4 = FY − (Q1+Q2+Q3)).
    derived: dict[str, str]


class TtmPeriod(BaseModel):
    start: str | None
    end: str | None
    derivation: str | None = None


class TtmBasis(BaseModel):
    periods: list[TtmPeriod]
    # Non-null exactly when the metric's TTM is null (e.g. missing or non-contiguous quarters).
    reason: str | None


class QuarterlyFinancials(BaseModel):
    workspace_id: str
    target_name: str
    # "unavailable" = quarterly extraction not stored for this workspace (refresh required).
    source_status: Literal["available", "unavailable"]
    source_note: str | None
    quarters: list[QuarterRow]
    ttm: dict[str, float | None]
    ttm_basis: dict[str, TtmBasis]
    generated_at: datetime
