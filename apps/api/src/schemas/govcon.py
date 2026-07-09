from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel


class GovConRequest(BaseModel):
    # Optional override; defaults to the target company name.
    recipient_name: str | None = None


class AgencyShare(BaseModel):
    agency: str | None
    amount: float
    pct: float | None


class GovConAward(BaseModel):
    award_id: str | None
    recipient: str | None
    agency: str | None
    sub_agency: str | None
    amount: float | None
    description: str
    pop_end: str | None
    pop_start: str | None


class RecompeteAward(BaseModel):
    award_id: str | None
    agency: str | None
    amount: float | None
    pop_end: str | None


class Recompete(BaseModel):
    count: int
    value: float
    awards: list[RecompeteAward]


class GovConProfileOut(ORMModel):
    id: str
    workspace_id: str
    recipient_name: str
    total_obligations: float
    award_count: int
    top_agency: str | None
    top_agency_pct: float | None
    agency_concentration: list[AgencyShare]
    top_awards: list[GovConAward]
    recompete: Recompete
    created_at: datetime
