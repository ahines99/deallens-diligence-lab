"""Shared router dependencies."""
from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.config import settings
from src.db.session import get_session
from src.schemas.identity import PrincipalContext

SessionDep = Annotated[Session, Depends(get_session)]


def optional_principal(request: Request) -> PrincipalContext | None:
    return getattr(request.state, "principal", None)


def require_scope(scope: str) -> Callable[[Request], None]:
    """Build a dependency that enforces an API-key scope on a route (G38).

    Human sessions and trusted-service callers (``principal.scopes is None``) are unaffected —
    they are already gated by role and the tenant guard. An API-key principal must have been
    granted ``scope`` or the request is rejected with 403. When authentication is disabled
    (``principal is None``) the check is a no-op, matching the rest of the dev-mode surface.
    """

    def _dependency(request: Request) -> None:
        principal = getattr(request.state, "principal", None)
        if principal is None or principal.has_scope(scope):
            return
        raise HTTPException(
            status_code=403, detail=f"API key is missing the required scope: {scope}"
        )

    return _dependency


def require_capability(capability: str) -> Callable[[Request], None]:
    """Build a dependency enforcing a fine-grained capability on a route (G49, deny-by-default).

    When authentication is disabled (``principal is None``, dev/auth-off) the check is a no-op, and
    when the ``PERMISSION_MATRIX_ENABLED`` toggle is off it degrades to the coarse role guard only.
    Otherwise the resolved principal must hold ``capability`` (its role default plus/minus any
    per-membership grant/revoke) or the request is rejected with 403. This narrows a route beyond
    the coarse viewer-read-only backstop, which still applies on top.
    """

    def _dependency(request: Request) -> None:
        if not settings.permission_matrix_enabled:
            return
        principal = getattr(request.state, "principal", None)
        if principal is None or principal.has_capability(capability):
            return
        raise HTTPException(
            status_code=403,
            detail=f"Membership lacks the required capability: {capability}",
        )

    return _dependency


def required_principal(request: Request) -> PrincipalContext:
    principal = optional_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authenticated principal required")
    return principal


OptionalPrincipalDep = Annotated[PrincipalContext | None, Depends(optional_principal)]
PrincipalDep = Annotated[PrincipalContext, Depends(required_principal)]
