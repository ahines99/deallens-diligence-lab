"""Schemas for Quality-of-Earnings + financial forensics (mirror of types.ts `Forensics`)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ForensicRating = Literal["strong", "neutral", "weak", "distress", "elevated", "n/a"]


class ForensicComponent(BaseModel):
    name: str
    value: float | None


class ForensicScore(BaseModel):
    key: str  # "altman_z", "piotroski_f", "beneish_m", "accruals"
    label: str
    value: float | None
    rating: ForensicRating
    interpretation: str
    components: list[ForensicComponent]
    available: bool
    note: str | None = None


class QoEMetric(BaseModel):
    key: str
    label: str
    unit: Literal["pct", "x", "usd", "days", "ratio"]
    value: float | None
    commentary: str


class Forensics(BaseModel):
    workspace_id: str
    target_name: str
    as_of_year: str | None
    scores: list[ForensicScore]
    qoe: list[QoEMetric]
    notes: list[str]
    generated_at: datetime
