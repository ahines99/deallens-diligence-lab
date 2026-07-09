"""Risk / red-flag generation — delegates to the full analysis pass, reads persisted findings."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import RiskFinding
from src.services import analysis_service


def generate(session: Session, workspace_id: str) -> list[RiskFinding]:
    analysis_service.run_full_analysis(session, workspace_id)
    return list_risks(session, workspace_id)


def list_risks(session: Session, workspace_id: str) -> list[RiskFinding]:
    return list(
        session.scalars(
            select(RiskFinding)
            .where(RiskFinding.workspace_id == workspace_id)
            .order_by(RiskFinding.severity_score.desc())
        )
    )
