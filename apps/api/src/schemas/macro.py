from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MacroPoint(BaseModel):
    date: str
    value: float


class MacroSeries(BaseModel):
    series_id: str
    label: str
    unit: str
    note: str
    latest_value: float
    latest_date: str
    yoy_change: float | None
    points: list[MacroPoint]


class MacroOverlay(BaseModel):
    workspace_id: str
    target_name: str
    sector: str
    commentary: str
    series: list[MacroSeries]
    generated_at: datetime
