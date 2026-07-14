from __future__ import annotations

from datetime import datetime

from src.schemas.common import MemoType, ORMModel


class MemoOut(ORMModel):
    id: str
    workspace_id: str
    memo_type: MemoType
    title: str
    markdown_content: str
    created_at: datetime
    updated_at: datetime


class MemoFaithfulnessDocument(ORMModel):
    document_type: str
    citation_count: int
    distinct_refs: int
    unresolved_refs: list[str]
    numeric_token_count: int
    uncited_numeric_sentences: list[str]
    uncited_numeric_sentence_count: int
    fully_resolved: bool


class MemoFaithfulnessReport(ORMModel):
    workspace_id: str
    evidence_ref_count: int
    documents: list[MemoFaithfulnessDocument]
    generated_at: str
