from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.risk import RiskOut
from src.services import risk_extraction_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["risks"])


@router.post("/{workspace_id}/risks/generate", response_model=list[RiskOut])
def generate_risks(workspace_id: str, session: SessionDep) -> list[RiskOut]:
    return [RiskOut.model_validate(r) for r in risk_extraction_service.generate(session, workspace_id)]


@router.get("/{workspace_id}/risks", response_model=list[RiskOut])
def list_risks(workspace_id: str, session: SessionDep) -> list[RiskOut]:
    get_workspace_or_404(session, workspace_id)
    return [RiskOut.model_validate(r) for r in risk_extraction_service.list_risks(session, workspace_id)]
