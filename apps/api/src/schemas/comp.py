from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.common import ORMModel


class CompCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    company_name: str = ""
    sector: str = ""
    business_description: str = ""
    revenue: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    revenue_growth: float | None = None
    rnd_pct: float | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None
    ev_revenue_multiple: float | None = None
    notes: str = ""


class CompsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Add real public peers by ticker (fetched from SEC XBRL), or pass explicit comp rows.
    tickers: list[str] = Field(default_factory=list)
    comps: list[CompCreate] | None = None


class CompOut(ORMModel):
    id: str
    workspace_id: str
    ticker: str
    company_name: str
    sector: str
    business_description: str
    revenue: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    revenue_growth: float | None
    rnd_pct: float | None
    market_cap: float | None
    enterprise_value: float | None
    ev_revenue_multiple: float | None
    notes: str
    data_source: str
    is_illustrative: bool


class BenchmarkMetric(BaseModel):
    key: str
    label: str
    unit: Literal["pct", "x", "usd", "ratio"]
    target_value: float | None
    peer_median: float | None
    peer_min: float | None
    peer_max: float | None
    assessment: Literal["above", "in_line", "below", "n/a"]
    commentary: str


class FinancialBenchmark(BaseModel):
    workspace_id: str
    target_name: str
    peer_count: int
    summary: str
    metrics: list[BenchmarkMetric]
    notes: list[str]
    generated_at: datetime
