from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.forensics import Forensics
from src.services import forensics_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["forensics"])


@router.get("/{workspace_id}/forensics", response_model=Forensics)
def get_forensics(workspace_id: str, session: SessionDep) -> Forensics:
    get_workspace_or_404(session, workspace_id)
    return Forensics.model_validate(forensics_service.compute_forensics(session, workspace_id))
