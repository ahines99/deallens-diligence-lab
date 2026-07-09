from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.filing import FilingOut
from src.services import sec_ingestion_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["filings"])


@router.get("/{workspace_id}/filings", response_model=list[FilingOut])
def list_filings(workspace_id: str, session: SessionDep) -> list[FilingOut]:
    get_workspace_or_404(session, workspace_id)
    return [FilingOut.model_validate(f) for f in sec_ingestion_service.list_filings(session, workspace_id)]
