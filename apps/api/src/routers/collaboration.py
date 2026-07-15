"""HTTP surface for collaboration & governance UX: review inbox (G42) and audit explorer (G43)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.deal_workflow import WorkflowAuditOut
from src.schemas.review_inbox import ReviewInboxOut
from src.services import audit_explorer_service
from src.services import review_inbox_service

router = APIRouter(prefix="/api/organizations", tags=["collaboration"])


def _authorize(organization_id: str, principal) -> None:
    if principal is not None and principal.organization_id != organization_id:
        # Tenant identifiers are intentionally non-enumerable across memberships.
        raise HTTPException(status_code=404, detail="Organization not found")


@router.get("/{organization_id}/my-reviews", response_model=ReviewInboxOut)
def get_my_reviews(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    actor_id: str | None = Query(default=None, max_length=200),
) -> ReviewInboxOut:
    """One queue of QoE, claim, diligence, and IC-comment items awaiting the signed-in actor."""
    _authorize(organization_id, principal)
    resolved_actor = actor_id or (principal.user_id if principal is not None else None)
    try:
        result = review_inbox_service.my_reviews(session, organization_id, resolved_actor)
    except review_inbox_service.ReviewInboxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return ReviewInboxOut.model_validate(result)


@router.get("/{organization_id}/audit-events", response_model=list[WorkflowAuditOut])
def list_audit_events(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    actor_id: str | None = Query(default=None, max_length=200),
    entity_type: str | None = Query(default=None, max_length=80),
    entity_id: str | None = Query(default=None, max_length=32),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=audit_explorer_service.DEFAULT_LIMIT, ge=1, le=audit_explorer_service.MAX_LIMIT),
) -> list[WorkflowAuditOut]:
    """Organization-level audit explorer filtered by actor / entity / date window."""
    _authorize(organization_id, principal)
    events = audit_explorer_service.list_events(
        session,
        organization_id,
        actor=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
        limit=limit,
    )
    return [WorkflowAuditOut.model_validate(event) for event in events]


@router.get("/{organization_id}/audit-events/export.csv")
def export_audit_events_csv(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    actor_id: str | None = Query(default=None, max_length=200),
    entity_type: str | None = Query(default=None, max_length=80),
    entity_id: str | None = Query(default=None, max_length=32),
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    """Export the filtered audit view as formula-injection-safe CSV."""
    _authorize(organization_id, principal)
    csv_text = audit_explorer_service.export_csv(
        session,
        organization_id,
        actor=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
    )
    return Response(
        csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="audit-events-{organization_id}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
