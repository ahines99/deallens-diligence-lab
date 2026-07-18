"""Pydantic schemas for G66 — buyback & dilution analysis.

Per-fiscal-year shares outstanding, SBC expense, common-stock repurchases, and net YoY share
change from XBRL company facts, CY-frame keyed. A concept a filer did not tag for a year is
``None`` — never interpolated — and each derived value carries an XBRL citation
(concept / period end / accession / form).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DilutionYear(BaseModel):
    shares_out: float | None
    sbc: float | None
    repurchases: float | None
    # (shares_y - shares_{y-1}) / shares_{y-1} across CONSECUTIVE tagged fiscal years only;
    # positive = net dilution, negative = net reduction. None when either year is untagged.
    net_dilution_pct: float | None


class DilutionCitation(BaseModel):
    concept: str
    end: str | None = None
    accession: str | None = None
    form: str | None = None


class DilutionAnalysis(BaseModel):
    workspace_id: str
    target_name: str
    status: Literal["available", "partial", "unavailable"]
    years: list[str]
    by_year: dict[str, DilutionYear]
    # year -> field -> citation, only for tagged points (an absent field-year has no citation).
    citations: dict[str, dict[str, DilutionCitation]]
    # field -> the us-gaap concept actually used (None when no concept in the family is tagged).
    sources: dict[str, str | None]
    note: str
    source_error: str | None = None
    generated_at: datetime
