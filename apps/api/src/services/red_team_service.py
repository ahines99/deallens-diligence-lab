"""Red-team / bear-case — read persisted report; (re)build via the full analysis pass."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import RedTeamReport
from src.services import analysis_service
from src.services.common import NotFound


def generate(session: Session, workspace_id: str) -> RedTeamReport:
    analysis_service.run_full_analysis(session, workspace_id)
    return get(session, workspace_id)


def get(session: Session, workspace_id: str) -> RedTeamReport:
    report = session.scalar(select(RedTeamReport).where(RedTeamReport.workspace_id == workspace_id))
    if report is None:
        raise NotFound("Red-team analysis not generated yet.")
    return report
