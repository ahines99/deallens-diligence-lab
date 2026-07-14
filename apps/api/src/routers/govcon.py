from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import SessionDep
from src.schemas.govcon import GovConProfileOut, GovConRequest
from src.services import analysis_service, govcon_service, workspace_service
from src.services.common import get_workspace_or_404
from src.services.usaspending_service import UsaSpendingError

router = APIRouter(prefix="/api/workspaces", tags=["govcon"])


@router.post("/{workspace_id}/govcon", response_model=GovConProfileOut)
def generate_govcon(workspace_id: str, payload: GovConRequest, session: SessionDep) -> GovConProfileOut:
    get_workspace_or_404(session, workspace_id)
    try:
        govcon_service.fetch(session, workspace_id, payload.recipient_name)
        session.commit()
        # Fold GovCon findings/questions/memo section into the pack.
        if workspace_service.get_target(session, workspace_id) is not None:
            analysis_service.run_full_analysis(session, workspace_id)
    except UsaSpendingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return GovConProfileOut.model_validate(govcon_service.get(session, workspace_id))


@router.get("/{workspace_id}/govcon", response_model=GovConProfileOut)
def get_govcon(workspace_id: str, session: SessionDep) -> GovConProfileOut:
    get_workspace_or_404(session, workspace_id)
    return GovConProfileOut.model_validate(govcon_service.get(session, workspace_id))
