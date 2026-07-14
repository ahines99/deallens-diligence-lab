"""Workspace lifecycle: create (optionally ingesting a real ticker), read, and overview."""
from __future__ import annotations

import logging

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

logger = logging.getLogger("deallens.workspace")


def create_workspace(
    session: Session,
    data: WorkspaceCreate,
    *,
    organization_id: str | None = None,
    defer_build: bool = False,
) -> Workspace:
    """Create a workspace, optionally ingesting a public ticker.

    With ``defer_build=True`` the workspace is returned immediately in a ``building``
    state and the caller is responsible for scheduling ``run_build`` (the API path).
    The default remains fully synchronous for seeds, scripts, and direct service use.
    """
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
        build_status="building" if ticker else "ready",
        build_ticker=ticker or None,
    )
    session.add(ws)
    session.commit()

    if ticker and not defer_build:
        run_build(session, ws.id)
        if session.scalar(
            select(Workspace.build_status).where(Workspace.id == ws.id)
        ) == "failed":
            error = session.scalar(
                select(Workspace.build_error).where(Workspace.id == ws.id)
            )
            raise edgar_client.EdgarError(error or f"Ingestion failed for {ticker}")

    session.refresh(ws)
    return ws


def get_build_status(session: Session, workspace_id: str) -> dict:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    return {
        "workspace_id": ws.id,
        "status": ws.build_status,
        "step": ws.build_step,
        "error": ws.build_error,
        "ticker": ws.build_ticker,
    }


def retry_build(session: Session, workspace_id: str) -> dict:
    """Re-arm a failed build so the caller can schedule ``run_build`` again."""
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    if ws.build_status != "failed":
        raise ValueError(f"Workspace build is '{ws.build_status}', not 'failed'; nothing to retry")
    if not ws.build_ticker:
        raise ValueError("Workspace has no ticker to rebuild from")
    ws.build_status = "building"
    ws.build_step = None
    ws.build_error = None
    session.commit()
    return get_build_status(session, workspace_id)


def run_build(session: Session, workspace_id: str) -> None:
    """Run the full ticker ingest + analysis, recording per-step progress on the workspace.

    Never raises: failures are recorded as ``build_status='failed'`` with the error message,
    since this usually executes outside a request (background task) with no caller to catch.
    """
    ws = session.get(Workspace, workspace_id)
    if ws is None or not ws.build_ticker:
        return
    ticker = ws.build_ticker

    def progress(step: str) -> None:
        # Committing at step boundaries makes progress visible to concurrent status polls
        # (ingestion is idempotent, so a partially committed build is safe to re-run).
        ws.build_step = step
        session.commit()

    try:
        from src.services import analysis_service

        sec_ingestion_service.ingest_company(session, workspace_id, ticker, progress=progress)
        session.commit()
        progress("running_analysis")
        analysis_service.run_full_analysis(session, workspace_id)
        ws.build_status = "ready"
        ws.build_step = None
        ws.build_error = None
        session.commit()
    except Exception as exc:  # noqa: BLE001 — every failure must land in build_status
        logger.exception("Workspace %s build failed during %s", workspace_id, ws.build_step)
        session.rollback()
        ws.build_status = "failed"
        ws.build_error = str(exc) or exc.__class__.__name__
        session.commit()


def run_build_in_new_session(workspace_id: str) -> None:
    """Background-task entry point: builds with a session independent of the request's."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        run_build(session, workspace_id)


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
