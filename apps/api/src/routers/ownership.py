"""Ownership signals: 13F institutional-ownership concentration + 13D/13G activist stakes.

Kept in a dedicated router (rather than appended to signals.py) so the two ownership feeds evolve
independently. Both endpoints are keyless, read-only, and preserve the source_status discipline.
"""
from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.ownership import ActivistStakes, InstitutionalOwnership
from src.services import ownership_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["ownership"])


@router.get("/{workspace_id}/institutional-ownership", response_model=InstitutionalOwnership)
def get_institutional_ownership(workspace_id: str, session: SessionDep) -> InstitutionalOwnership:
    get_workspace_or_404(session, workspace_id)
    return InstitutionalOwnership.model_validate(
        ownership_service.institutional_ownership(session, workspace_id)
    )


@router.get("/{workspace_id}/activist-stakes", response_model=ActivistStakes)
def get_activist_stakes(workspace_id: str, session: SessionDep) -> ActivistStakes:
    get_workspace_or_404(session, workspace_id)
    return ActivistStakes.model_validate(ownership_service.activist_stakes(session, workspace_id))
