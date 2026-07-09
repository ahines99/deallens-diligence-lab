from __future__ import annotations

from datetime import datetime

from src.schemas.common import ORMModel, Priority, RiskCategory, Severity, Workstream


class RiskOut(ORMModel):
    id: str
    workspace_id: str
    risk_category: RiskCategory
    risk_category_label: str
    title: str
    finding: str
    severity: Severity
    severity_score: int
    likelihood: Priority
    confidence: float
    evidence_ref: str | None
    follow_up_question: str
    workstream_owner: Workstream
    created_at: datetime
