from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.schemas.common import Workstream


class PlanWorkstream(BaseModel):
    workstream: Workstream
    workstream_label: str
    objective: str
    key_questions: list[str] = []
    evidence_needed: list[str] = []
    status: Literal["planned", "in_progress", "complete"] = "planned"


class DiligencePlanOut(BaseModel):
    workspace_id: str
    investment_question: str
    summary: str
    workstreams: list[PlanWorkstream]
    generated_at: datetime
