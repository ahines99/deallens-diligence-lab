from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from src.routers.deps import OptionalPrincipalDep, PrincipalDep, SessionDep
from src.schemas.identity import WorkspaceGovernancePatch
from src.schemas.plan import DiligencePlanOut
from src.schemas.workspace import (
    WorkspaceBuildStatus,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceOverview,
)
from src.services import diligence_question_service, job_service, workspace_service
from src.services.edgar_client import EdgarError

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    background: BackgroundTasks,
) -> WorkspaceOut:
    try:
        # Ticker resolution stays synchronous so an unknown ticker still 404s immediately;
        # the slow ingest + analysis runs after the response, surfaced via build-status.
        ws = workspace_service.create_workspace(
            session,
            payload,
            organization_id=principal.organization_id if principal else None,
            defer_build=True,
        )
    except EdgarError as exc:
        status = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    if ws.build_status == "building":
        # Durable queue first (a crashed process can never lose the build), then an
        # in-process drain so single-process deployments finish without a separate worker.
        job = job_service.enqueue(session, "workspace_build", {"workspace_id": ws.id})
        background.add_task(job_service.drain_job_in_new_session, job.id)
    return WorkspaceOut.model_validate(ws)


@router.get("/{workspace_id}/build-status", response_model=WorkspaceBuildStatus)
def get_build_status(workspace_id: str, session: SessionDep) -> WorkspaceBuildStatus:
    return WorkspaceBuildStatus.model_validate(
        workspace_service.get_build_status(session, workspace_id)
    )


@router.post("/{workspace_id}/build/retry", response_model=WorkspaceBuildStatus)
def retry_build(
    workspace_id: str, session: SessionDep, background: BackgroundTasks
) -> WorkspaceBuildStatus:
    try:
        status = workspace_service.retry_build(session, workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job = job_service.enqueue(session, "workspace_build", {"workspace_id": workspace_id})
    background.add_task(job_service.drain_job_in_new_session, job.id)
    return WorkspaceBuildStatus.model_validate(status)


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(
    session: SessionDep, principal: OptionalPrincipalDep
) -> list[WorkspaceOut]:
    organization_id = principal.organization_id if principal else None
    return [
        WorkspaceOut.model_validate(w)
        for w in workspace_service.list_workspaces(session, organization_id)
    ]


@router.patch("/{workspace_id}/governance", response_model=WorkspaceOut)
def update_workspace_governance(
    workspace_id: str,
    payload: WorkspaceGovernancePatch,
    session: SessionDep,
    principal: PrincipalDep,
) -> WorkspaceOut:
    try:
        workspace = workspace_service.update_governance(
            session, workspace_id, payload, principal
        )
    except ValueError as exc:
        status_code = getattr(exc, "status_code", 400)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceOverview)
def get_workspace(workspace_id: str, session: SessionDep) -> WorkspaceOverview:
    return WorkspaceOverview.model_validate(workspace_service.get_overview(session, workspace_id))


@router.post("/{workspace_id}/plan/generate", response_model=DiligencePlanOut)
def generate_plan(workspace_id: str, session: SessionDep) -> DiligencePlanOut:
    return DiligencePlanOut.model_validate(
        diligence_question_service.generate_plan(session, workspace_id)
    )


@router.get("/{workspace_id}/plan", response_model=DiligencePlanOut)
def get_plan(workspace_id: str, session: SessionDep) -> DiligencePlanOut:
    return DiligencePlanOut.model_validate(diligence_question_service.get_plan(session, workspace_id))
