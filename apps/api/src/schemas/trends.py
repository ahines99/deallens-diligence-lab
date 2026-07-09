from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TrendPoint(BaseModel):
    year: str
    revenue: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    rnd_pct: float | None


class FinancialTrends(BaseModel):
    workspace_id: str
    target_name: str
    years: list[str]
    rows: list[TrendPoint]
    revenue_cagr: float | None
    generated_at: datetime
