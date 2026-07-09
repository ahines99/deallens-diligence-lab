from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel, TargetType


class TargetCreate(BaseModel):
    name: str
    target_type: TargetType = "public_company"
    ticker: str | None = None
    cik: str | None = None
    sector: str = ""
    description: str = ""
    revenue: float | None = None
    revenue_growth: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_income: float | None = None
    net_margin: float | None = None
    rnd_pct: float | None = None
    rule_of_40: float | None = None
    cash: float | None = None
    total_debt: float | None = None
    headcount: int | None = None
    fiscal_year_end: str | None = None
    data_source: str = "SEC EDGAR (XBRL)"
    is_synthetic: bool = False
    financials: dict | None = None


class TargetOut(ORMModel):
    id: str
    name: str
    target_type: str
    ticker: str | None
    cik: str | None
    sector: str
    description: str
    revenue: float | None
    revenue_growth: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_income: float | None
    net_margin: float | None
    rnd_pct: float | None
    rule_of_40: float | None
    cash: float | None
    total_debt: float | None
    headcount: int | None
    fiscal_year_end: str | None
    data_source: str
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime
