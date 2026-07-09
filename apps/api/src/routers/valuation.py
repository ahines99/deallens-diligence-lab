from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.valuation import LboInputs, LboResult, Valuation
from src.services import valuation_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["valuation"])


@router.get("/{workspace_id}/valuation", response_model=Valuation)
def get_valuation(workspace_id: str, session: SessionDep) -> Valuation:
    get_workspace_or_404(session, workspace_id)
    return Valuation.model_validate(valuation_service.compute_valuation(session, workspace_id))


@router.post("/{workspace_id}/lbo", response_model=LboResult)
def post_lbo(workspace_id: str, payload: LboInputs, session: SessionDep) -> LboResult:
    get_workspace_or_404(session, workspace_id)
    result = valuation_service.run_lbo(session, workspace_id, payload.model_dump())
    return LboResult.model_validate(result)
