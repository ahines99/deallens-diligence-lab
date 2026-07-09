"""Financial analyst — owns deterministic financial calculations and financial evidence.

Calculations here are pure Python (auditable), never delegated to an LLM.
"""
from __future__ import annotations

from src.agents.base import BaseAgent


class FinancialAnalyst(BaseAgent):
    name = "financial_analyst"
    role = "Computes financial metrics and benchmarks; all math is deterministic."

    @staticmethod
    def rule_of_40(revenue_growth: float | None, ebitda_margin: float | None) -> float | None:
        if revenue_growth is None or ebitda_margin is None:
            return None
        return round(revenue_growth + ebitda_margin, 4)

    @staticmethod
    def implied_ebitda(revenue: float | None, ebitda_margin: float | None) -> float | None:
        if revenue is None or ebitda_margin is None:
            return None
        return round(revenue * ebitda_margin, 2)

    @staticmethod
    def median(values: list[float]) -> float | None:
        vals = sorted(v for v in values if v is not None)
        if not vals:
            return None
        n = len(vals)
        mid = n // 2
        if n % 2:
            return vals[mid]
        return round((vals[mid - 1] + vals[mid]) / 2, 6)
