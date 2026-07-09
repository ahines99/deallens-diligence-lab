from __future__ import annotations

from datetime import datetime

from src.schemas.common import ORMModel, Priority, Workstream


class QuestionOut(ORMModel):
    id: str
    workspace_id: str
    workstream: Workstream
    workstream_label: str
    question: str
    rationale: str
    priority: Priority
    evidence_ref: str | None
    created_at: datetime
