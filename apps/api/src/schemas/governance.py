from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel


class ExecCompRow(BaseModel):
    name: str
    title: str | None = None
    salary: float | None = None
    bonus: float | None = None
    stock_awards: float | None = None
    total: float | None = None


class GovernanceRedFlag(BaseModel):
    flag: str
    label: str
    present: bool
    evidence: str | None = None


class GovernanceProfileOut(ORMModel):
    id: str
    workspace_id: str
    def14a_accession: str | None
    filing_date: str | None
    exec_comp: list[ExecCompRow]
    red_flags: list[GovernanceRedFlag]
    source_status: str
    raw_note: str | None
    created_at: datetime
