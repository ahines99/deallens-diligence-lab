"""Schemas for the long-term-debt maturity schedule / "maturity wall" (G16)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DebtMaturityRow(BaseModel):
    # Year bucket: Y1 (next twelve months) .. Y5, then "thereafter".
    bucket: str
    amount: float
    source_concept: str
    period_end: str


class DebtMaturitySchedule(BaseModel):
    workspace_id: str
    target_name: str
    # available = every bucket tagged as of the balance-sheet date; partial = some buckets tagged
    # and others absent (never zero-filled); unavailable = no maturity concepts tagged, or a legacy
    # workspace with no stored key (refresh required) — see source_note.
    source_status: Literal["available", "partial", "unavailable"]
    source_note: str | None
    # The balance-sheet date the schedule is tagged as of (null when unavailable).
    as_of: str | None
    schedule: list[DebtMaturityRow]
    # Sum of ONLY the tagged buckets — not a claim about total debt when buckets are missing.
    total_scheduled: float | None
    # Year buckets the filer did not tag (reported, never imputed).
    missing_buckets: list[str]
    generated_at: datetime
