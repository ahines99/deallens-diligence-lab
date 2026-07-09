from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.red_team import RedTeamOut
from src.services import red_team_service

router = APIRouter(prefix="/api/workspaces", tags=["red-team"])


@router.post("/{workspace_id}/red-team/generate", response_model=RedTeamOut)
def generate_red_team(workspace_id: str, session: SessionDep) -> RedTeamOut:
    return RedTeamOut.model_validate(red_team_service.generate(session, workspace_id))


@router.get("/{workspace_id}/red-team", response_model=RedTeamOut)
def get_red_team(workspace_id: str, session: SessionDep) -> RedTeamOut:
    return RedTeamOut.model_validate(red_team_service.get(session, workspace_id))
