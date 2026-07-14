from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import OptionalPrincipalDep, PrincipalDep, SessionDep
from src.schemas.identity import WorkspaceGovernancePatch
from src.schemas.plan import DiligencePlanOut
from src.schemas.workspace import WorkspaceCreate, WorkspaceOut, WorkspaceOverview
from src.services import diligence_question_service, workspace_service
from src.services.edgar_client import EdgarError

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> WorkspaceOut:
    try:
        ws = workspace_service.create_workspace(
            session,
            payload,
            organization_id=principal.organization_id if principal else None,
        )
    except EdgarError as exc:
        status = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return WorkspaceOut.model_validate(ws)


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
