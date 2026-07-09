from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.evidence import EvidenceOut
from src.services import evidence_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["evidence"])


@router.get("/{workspace_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(workspace_id: str, session: SessionDep) -> list[EvidenceOut]:
    get_workspace_or_404(session, workspace_id)
    return [EvidenceOut.model_validate(e) for e in evidence_service.list_evidence(session, workspace_id)]
