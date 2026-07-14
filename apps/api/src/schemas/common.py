"""Shared literal types and base config for API schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

DealType = Literal[
    "buyout", "growth_equity", "private_credit", "public_equity", "govcon", "software_platform"
]
WorkspaceStatus = Literal["draft", "in_progress", "complete"]
TargetType = Literal["public_company", "private_company", "synthetic_private"]
Severity = Literal["low", "medium", "high", "critical"]
Priority = Literal["low", "medium", "high"]
ClaimType = Literal["fact", "calculation", "inference", "assumption"]
MemoType = Literal["ic_memo", "bear_case"]
RiskCategory = Literal[
    "customer_concentration",
    "supplier_concentration",
    "demand_weakness",
    "margin_pressure",
    "debt_liquidity",
    "legal_regulatory",
    "cyber_security",
    "integration_ma",
    "ai_tech_disruption",
    "govcon_risk",
]
Workstream = Literal[
    "commercial",
    "product_technology",
    "financial",
    "customer",
    "market",
    "legal_regulatory",
    "cybersecurity",
    "ai_data",
    "management",
    "govcon",
]


class ORMModel(BaseModel):
    """Base for schemas read from ORM objects."""

    model_config = ConfigDict(from_attributes=True)
