"""Shared service helpers: not-found handling and workspace lookups."""
from __future__ import annotations

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


def touch_status(ws: Workspace, status: str) -> None:
    """Advance a workspace's status if it's moving forward (draft -> in_progress -> complete)."""
    order = {"draft": 0, "in_progress": 1, "complete": 2}
    if order.get(status, 0) >= order.get(ws.status, 0):
        ws.status = status
