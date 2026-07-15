from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    question: str = Field(min_length=1, max_length=2_000)


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
    coverage: float = 0.0
    abstention_reason: str | None


class FilingsQAOut(BaseModel):
    workspace_id: str
    question: str
    status: str  # answered | partial | abstained
    answer: str
    citations: list[FilingsQACitation]
    retrieval: FilingsQARetrieval
    method: str
    generated_at: str


# --- G07: cross-year 10-K risk-factor drift -----------------------------------------------

class RiskDiffFilingRef(BaseModel):
    filing_id: str
    form_type: str
    filing_date: str
    document_url: str | None


class RiskDiffCitation(BaseModel):
    filing_id: str
    form_type: str | None
    filing_date: str | None
    section: str
    document_url: str | None
    chunk_index: int
    quote: str


class RiskDiffChange(BaseModel):
    old: RiskDiffCitation
    new: RiskDiffCitation
    similarity: float


class RiskDiffOut(BaseModel):
    workspace_id: str
    source_status: str  # ok | unavailable
    note: str
    older_filing: RiskDiffFilingRef | None
    newer_filing: RiskDiffFilingRef | None
    added: list[RiskDiffCitation]
    removed: list[RiskDiffCitation]
    changed: list[RiskDiffChange]
    method: str
    generated_at: str


# --- G08: unified cross-corpus Q&A --------------------------------------------------------

class CrossCorpusQARequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)


class CrossCorpusCitation(BaseModel):
    corpus: Literal["public_filing", "confidential_dataroom"]
    confidential: bool
    label: str
    quote: str
    source_name: str
    provenance: dict[str, Any]


class CrossCorpusQAOut(BaseModel):
    workspace_id: str
    deal_id: str | None
    question: str
    status: str  # answered | partial | abstained
    answer: str
    citations: list[CrossCorpusCitation]
    corpora: dict[str, Any]
    retrieval: dict[str, Any]
    method: str
    generated_at: str
