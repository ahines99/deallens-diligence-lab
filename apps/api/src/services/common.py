"""Shared service helpers: not-found handling and workspace lookups."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import Workspace


class NotFound(Exception):
    """Raised by services when a requested resource does not exist. Mapped to HTTP 404."""

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message)
        self.message = message


def get_workspace_or_404(session: Session, workspace_id: str) -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    return ws


def get_workspace_scoped_or_404(
    session: Session, workspace_id: str, organization_id: str | None
) -> Workspace:
    """Fetch a workspace, enforcing the caller's tenant boundary.

    Mirrors the ``/api/workspaces/{id}`` middleware for endpoints whose path does not match
    that prefix (e.g. body-addressed ``/api/sec/ingest``): a workspace is owned either directly
    (``organization_id``) or via its linked deal. Cross-tenant access returns the same 404 as a
    missing workspace so no existence oracle leaks. With no caller org (auth-off dev mode) the
    check degrades to a plain lookup, matching the middleware's behavior.
    """
    ws = get_workspace_or_404(session, workspace_id)
    if organization_id is None:
        return ws
    from src.models.deal_workflow import Deal

    effective_org = ws.organization_id or session.scalar(
        select(Deal.organization_id).where(Deal.workspace_id == workspace_id)
    )
    if effective_org != organization_id:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    return ws


def touch_status(ws: Workspace, status: str) -> None:
    """Advance a workspace's status if it's moving forward (draft -> in_progress -> complete)."""
    order = {"draft": 0, "in_progress": 1, "complete": 2}
    if order.get(status, 0) >= order.get(ws.status, 0):
        ws.status = status
