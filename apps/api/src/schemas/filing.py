from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel


class FilingOut(ORMModel):
    id: str
    workspace_id: str
    company_name: str
    ticker: str | None
    cik: str | None
    form_type: str
    filing_date: str
    accession_number: str | None
    document_url: str | None
    section_count: int
    is_synthetic: bool
    created_at: datetime


class SecSearchResult(BaseModel):
    cik: str
    ticker: str
    name: str


class SecIngestRequest(BaseModel):
    workspace_id: str
    ticker: str | None = None
    cik: str | None = None
    form_types: list[str] | None = None
    limit: int = 3
