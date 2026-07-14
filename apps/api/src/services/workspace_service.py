"""Workspace lifecycle: create (optionally ingesting a real ticker), read, and overview."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models import (
    ComparableCompany,
    DiligencePlan,
    DiligenceQuestion,
    Evidence,
    Filing,
    Memo,
    RedTeamReport,
    RiskFinding,
    Target,
    Workspace,
)
from src.schemas.target import TargetCreate
from src.schemas.identity import PrincipalContext, WorkspaceGovernancePatch
from src.schemas.workspace import WorkspaceCreate
from src.services import edgar_client, sec_ingestion_service
from src.services.common import NotFound


def create_workspace(
    session: Session,
    data: WorkspaceCreate,
    *,
    organization_id: str | None = None,
) -> Workspace:
    ticker = (data.ticker or "").strip().upper()
    name = data.name.strip()
    investment_question = data.investment_question.strip()

    if ticker:
        info = edgar_client.resolve_ticker(ticker)  # raises EdgarError if unknown
        if not name:
            name = f"{info['name']} ({info['ticker']}) Diligence"
        if not investment_question:
            investment_question = (
                f"Is {info['name']} ({info['ticker']}) an attractive investment at its current "
                f"financial profile and risk posture?"
            )
    elif not name:
        name = "Untitled workspace"

    ws = Workspace(
        name=name,
        organization_id=organization_id,
        deal_type=data.deal_type,
        investment_question=investment_question,
        status="draft",
    )
    session.add(ws)
    session.flush()

    if ticker:
        from src.services import analysis_service

        sec_ingestion_service.ingest_company(session, ws.id, ticker)
        session.commit()
        analysis_service.run_full_analysis(session, ws.id)

    session.commit()
    session.refresh(ws)
    return ws


def list_workspaces(session: Session, organization_id: str | None = None) -> list[Workspace]:
    statement = select(Workspace)
    if organization_id is not None:
        statement = statement.where(Workspace.organization_id == organization_id)
    return list(session.scalars(statement.order_by(Workspace.created_at.desc())))


def update_governance(
    session: Session,
    workspace_id: str,
    data: WorkspaceGovernancePatch,
    principal: PrincipalContext,
) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None or workspace.organization_id != principal.organization_id:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    if principal.role not in {"owner", "admin"}:
        from src.services.identity_service import IdentityForbidden

        raise IdentityForbidden("Only organization owners and admins can change governance")
    values = data.model_dump(exclude_unset=True)
    resulting_classification = values.get(
        "data_classification", workspace.data_classification
    )
    resulting_external_consent = values.get(
        "external_llm_allowed", workspace.external_llm_allowed
    )
    if resulting_classification == "restricted" and resulting_external_consent:
        raise ValueError("Restricted workspaces cannot enable external LLM processing")
    for key, value in values.items():
        setattr(workspace, key, value)
    session.commit()
    session.refresh(workspace)
    return workspace


def get_target(session: Session, workspace_id: str) -> Target | None:
    return session.scalar(select(Target).where(Target.workspace_id == workspace_id))


def set_target(session: Session, workspace_id: str, data: TargetCreate) -> Target:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    target = get_target(session, workspace_id)
    if target is None:
        target = Target(workspace_id=workspace_id)
        session.add(target)
    for field, value in data.model_dump().items():
        setattr(target, field, value)
    # Provenance is a server assertion. A client-created target is never represented as SEC data.
    target.data_source = "User-submitted target profile (unverified)"
    target.is_synthetic = False
    target.financials = None
    session.flush()
    ws.target_id = target.id
    session.commit()
    session.refresh(target)
    return target


def _count(session: Session, model, workspace_id: str) -> int:
    return session.scalar(
        select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
    ) or 0


def get_overview(session: Session, workspace_id: str) -> dict:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    target = get_target(session, workspace_id)

    counts = {
        "filings": _count(session, Filing, workspace_id),
        "comps": _count(session, ComparableCompany, workspace_id),
        "risks": _count(session, RiskFinding, workspace_id),
        "questions": _count(session, DiligenceQuestion, workspace_id),
        "evidence": _count(session, Evidence, workspace_id),
    }
    artifacts = {
        "plan": _count(session, DiligencePlan, workspace_id) > 0,
        "risks": counts["risks"] > 0,
        "questions": counts["questions"] > 0,
        "ic_memo": session.scalar(
            select(func.count()).select_from(Memo).where(
                Memo.workspace_id == workspace_id, Memo.memo_type == "ic_memo"
            )
        ) > 0,
        "bear_case": _count(session, RedTeamReport, workspace_id) > 0,
    }
    top_risks = list(
        session.scalars(
            select(RiskFinding)
            .where(RiskFinding.workspace_id == workspace_id)
            .order_by(RiskFinding.severity_score.desc())
            .limit(3)
        )
    )
    return {
        "workspace": ws,
        "target": target,
        "counts": counts,
        "artifacts": artifacts,
        "top_risks": top_risks,
    }
