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


class FilingsQARequest(BaseModel):
    question: str


class FilingsQACitation(BaseModel):
    filing_id: str
    form_type: str | None
    filing_date: str | None
    section: str
    document_url: str | None
    quote: str
    retrieval_score: float


class FilingsQARetrieval(BaseModel):
    chunks_considered: int
    matched_terms: list[str]
    abstention_reason: str | None


class FilingsQAOut(BaseModel):
    workspace_id: str
    question: str
    status: str  # answered | abstained
    answer: str
    citations: list[FilingsQACitation]
    retrieval: FilingsQARetrieval
    method: str
    generated_at: str
