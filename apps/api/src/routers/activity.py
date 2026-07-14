"""Unified activity timeline endpoint."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.activity import ActivityTimeline
from src.services import activity_service as service

router = APIRouter(prefix="/api/organizations", tags=["activity"])


@router.get("/{organization_id}/activity", response_model=ActivityTimeline)
def get_activity(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    deal_id: str | None = Query(default=None, max_length=32),
    actor_id: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=40),
    before: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> ActivityTimeline:
    if principal is not None and principal.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    try:
        result = service.get_timeline(
            session,
            organization_id,
            deal_id=deal_id,
            actor_id=actor_id,
            category=category,
            before=before,
            limit=limit,
        )
    except service.ActivityNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ActivityTimeline.model_validate(result)


__all__ = ["router"]
