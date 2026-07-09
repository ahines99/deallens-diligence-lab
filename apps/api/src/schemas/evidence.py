from __future__ import annotations

from datetime import datetime

from src.schemas.common import ClaimType, ORMModel


class EvidenceOut(ORMModel):
    id: str
    workspace_id: str
    ref: str
    claim: str
    claim_type: ClaimType
    source_name: str
    source_type: str
    source_url: str | None
    source_date: str | None
    source_section: str | None
    evidence_text: str
    confidence: float
    agent_name: str
    created_at: datetime
