from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.schemas.common import DealType, ORMModel
from src.schemas.risk import RiskOut
from src.schemas.target import TargetOut


class WorkspaceCreate(BaseModel):
    # `ticker` drives real ingestion. `name`/`investment_question` are optional and default
    # from the resolved company when a ticker is given.
    ticker: str | None = None
    name: str = ""
    deal_type: DealType = "public_equity"
    investment_question: str = ""


class WorkspaceOut(ORMModel):
    id: str
    name: str
    organization_id: str | None
    target_id: str | None
    deal_type: str
    investment_question: str
    status: str
    data_classification: str
    external_llm_allowed: bool
    created_at: datetime
    updated_at: datetime


class WorkspaceCounts(BaseModel):
    filings: int = 0
    comps: int = 0
    risks: int = 0
    questions: int = 0
    evidence: int = 0


class ArtifactFlags(BaseModel):
    plan: bool = False
    risks: bool = False
    questions: bool = False
    ic_memo: bool = False
    bear_case: bool = False


class WorkspaceOverview(BaseModel):
    workspace: WorkspaceOut
    target: TargetOut | None = None
    counts: WorkspaceCounts
    artifacts: ArtifactFlags
    top_risks: list[RiskOut] = []
