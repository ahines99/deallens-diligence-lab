from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.governance import GovernanceProfileOut
from src.services import proxy_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["governance"])


@router.post("/{workspace_id}/governance-profile", response_model=GovernanceProfileOut)
def build_governance_profile(workspace_id: str, session: SessionDep) -> GovernanceProfileOut:
    """Fetch the target's most recent DEF 14A, parse exec comp + governance red flags, store.

    Re-runs on demand. Distinct from PATCH ``/governance`` (workspace data-classification consent).
    """
    get_workspace_or_404(session, workspace_id)
    profile = proxy_service.build_profile(session, workspace_id)
    session.commit()
    return GovernanceProfileOut.model_validate(profile)


@router.get("/{workspace_id}/governance-profile", response_model=GovernanceProfileOut)
def get_governance_profile(workspace_id: str, session: SessionDep) -> GovernanceProfileOut:
    get_workspace_or_404(session, workspace_id)
    return GovernanceProfileOut.model_validate(proxy_service.get(session, workspace_id))
