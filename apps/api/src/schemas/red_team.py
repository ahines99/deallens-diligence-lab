from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import ORMModel, Priority, Workstream


class UnsupportedClaim(BaseModel):
    claim: str
    why_weak: str
    recommended_action: str


class MissingEvidence(BaseModel):
    item: str
    why_it_matters: str
    workstream: Workstream


class RedTeamQuestion(BaseModel):
    workstream: Workstream
    workstream_label: str
    question: str
    rationale: str
    priority: Priority


class RedTeamOut(ORMModel):
    id: str
    workspace_id: str
    bear_case_markdown: str
    summary: str
    unsupported_claims: list[UnsupportedClaim]
    missing_evidence: list[MissingEvidence]
    high_priority_questions: list[RedTeamQuestion]
    created_at: datetime
