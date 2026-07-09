"""Schemas for valuation & returns: WACC, DCF-lite, and an LBO returns model.

Mirrors `apps/web/src/lib/types.ts` (WACC, DCF, Valuation, LboInputs, LboResult, LboSensitivity).
All money in USD; rates/ratios as decimals.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class WACC(BaseModel):
    value: float | None
    risk_free: float | None
    equity_risk_premium: float
    beta: float
    cost_of_equity: float | None
    cost_of_debt: float | None
    tax_rate: float
    debt_weight: float | None


class DCF(BaseModel):
    fcf_base: float | None
    growth: float
    terminal_growth: float
    wacc: float | None
    enterprise_value: float | None
    assumptions: list[str]


class Valuation(BaseModel):
    workspace_id: str
    target_name: str
    ebitda: float | None
    net_debt: float | None
    wacc: WACC
    dcf: DCF
    notes: list[str]
    generated_at: datetime


class LboInputs(BaseModel):
    entry_multiple: float  # EV / EBITDA at entry
    exit_multiple: float
    leverage: float  # entry net debt / EBITDA
    hold_years: int
    ebitda_cagr: float  # decimal


class LboSensitivity(BaseModel):
    entry_multiples: list[float]
    exit_multiples: list[float]
    irr_grid: list[list[float | None]]
    moic_grid: list[list[float | None]]


class LboResult(BaseModel):
    entry_ev: float | None
    entry_equity: float | None
    exit_ev: float | None
    exit_equity: float | None
    irr: float | None
    moic: float | None
    inputs: LboInputs
    sensitivity: LboSensitivity
    assumptions: list[str]
    generated_at: datetime
