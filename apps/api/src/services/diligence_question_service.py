"""Diligence plan + questions — read persisted artifacts; (re)build via the full analysis pass."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DiligencePlan, DiligenceQuestion
from src.services import analysis_service
from src.services.common import NotFound


def generate_plan(session: Session, workspace_id: str) -> dict:
    analysis_service.run_full_analysis(session, workspace_id)
    return get_plan(session, workspace_id)


def get_plan(session: Session, workspace_id: str) -> dict:
    plan = session.scalar(select(DiligencePlan).where(DiligencePlan.workspace_id == workspace_id))
    if plan is None:
        raise NotFound("Diligence plan not generated yet.")
    return {
        "workspace_id": plan.workspace_id,
        "investment_question": plan.investment_question,
        "summary": plan.summary,
        "workstreams": plan.workstreams,
        "generated_at": plan.updated_at,
    }


def generate_questions(session: Session, workspace_id: str) -> list[DiligenceQuestion]:
    analysis_service.run_full_analysis(session, workspace_id)
    return list_questions(session, workspace_id)


def list_questions(session: Session, workspace_id: str) -> list[DiligenceQuestion]:
    return list(
        session.scalars(
            select(DiligenceQuestion)
            .where(DiligenceQuestion.workspace_id == workspace_id)
            .order_by(DiligenceQuestion.priority.desc(), DiligenceQuestion.workstream)
        )
    )
