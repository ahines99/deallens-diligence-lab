"""Pydantic schemas for G64 — XBRL frames peer benchmarking.

Percentiles are ranked against the full SEC frames reporting universe (labeled in
``peer_scope``), never a fabricated SIC peer set; thin frames degrade to an explicit
"insufficient peer coverage" note with the coverage count preserved. Mirrors the source-status
discipline of ``apps/api/src/schemas/ownership.py``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PeerMetric(BaseModel):
    metric: str
    # From the workspace's STORED financials (the cited XBRL extraction), not the frame.
    target_value: float | None
    # Midrank percentile in [0, 1] over the peer universe; None when the target value is
    # unavailable or coverage is below the floor — never fabricated.
    percentile: float | None
    # Number of peer entities whose value was computable (target's own row excluded).
    coverage: int
    # The exact frame concept(s) the universe was built from.
    concepts: list[str]
    note: str


class PeerBenchmark(BaseModel):
    workspace_id: str
    target_name: str
    status: Literal["available", "partial", "unavailable"]
    as_of_year: int | None
    target_sic: str | None
    sic_description: str | None
    peer_scope: str | None
    metrics: list[PeerMetric]
    note: str
    source_error: str | None = None
    generated_at: datetime
