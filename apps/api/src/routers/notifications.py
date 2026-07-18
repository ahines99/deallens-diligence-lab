"""In-app notification center endpoints, fed by the workflow audit outbox.

Organization scoping mirrors ``portfolio.py``: tenant identifiers are non-enumerable, so a
principal reaching for another organization's notifications gets a 404 rather than a 403.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.notification import NotificationDigest, NotificationOut, UnreadCount
from src.services import notification_service as service
from src.services.common import NotFound

router = APIRouter(prefix="/api", tags=["notifications"])


def _authorize(organization_id: str, principal) -> None:
    if principal is not None and principal.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")


@router.get(
    "/organizations/{organization_id}/notifications",
    response_model=list[NotificationOut],
)
def list_notifications(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    unread_only: bool = Query(default=False),
) -> list[NotificationOut]:
    _authorize(organization_id, principal)
    # Lazily drain the audit outbox so the center is live without a dedicated worker; the sync
    # is idempotent, so a concurrent reader can never double-create a notification.
    service.sync_from_audit(session, organization_id)
    return [
        NotificationOut.model_validate(item)
        for item in service.list_notifications(
            session,
            organization_id,
            unread_only=unread_only,
            user_id=principal.user_id if principal else None,
        )
    ]


@router.get(
    "/organizations/{organization_id}/notifications/unread-count",
    response_model=UnreadCount,
)
def get_unread_count(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> UnreadCount:
    _authorize(organization_id, principal)
    service.sync_from_audit(session, organization_id)
    return UnreadCount(
        organization_id=organization_id,
        unread=service.unread_count(
            session, organization_id, user_id=principal.user_id if principal else None
        ),
    )


@router.get(
    "/organizations/{organization_id}/notifications/digest",
    response_model=NotificationDigest,
)
def get_notification_digest(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    window: str = Query(default="daily", pattern="^(daily|weekly)$"),
) -> NotificationDigest:
    """G77 — per-user daily/weekly digest: counts + top items by event type, directed rows,
    and a review-inbox aging summary. A read-only roll-up of the live list (nothing is marked
    read), honoring the same directed-notification privacy filter."""
    _authorize(organization_id, principal)
    service.sync_from_audit(session, organization_id)
    return NotificationDigest.model_validate(
        service.build_digest(
            session,
            organization_id,
            user_id=principal.user_id if principal else None,
            window=window,
        )
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_notification_read(
    notification_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> NotificationOut:
    try:
        notification = service.mark_read(
            session,
            notification_id,
            principal.organization_id if principal else None,
            user_id=principal.user_id if principal else None,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return NotificationOut.model_validate(notification)


__all__ = ["router"]
