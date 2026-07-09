from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.target import TargetCreate, TargetOut
from src.services import workspace_service
from src.services.common import NotFound

router = APIRouter(prefix="/api/workspaces", tags=["target"])


@router.get("/{workspace_id}/target", response_model=TargetOut)
def get_target(workspace_id: str, session: SessionDep) -> TargetOut:
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set for this workspace.")
    return TargetOut.model_validate(target)


@router.post("/{workspace_id}/target", response_model=TargetOut)
def set_target(workspace_id: str, payload: TargetCreate, session: SessionDep) -> TargetOut:
    return TargetOut.model_validate(workspace_service.set_target(session, workspace_id, payload))
