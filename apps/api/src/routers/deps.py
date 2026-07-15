"""Shared router dependencies."""
from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

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


def required_principal(request: Request) -> PrincipalContext:
    principal = optional_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authenticated principal required")
    return principal


OptionalPrincipalDep = Annotated[PrincipalContext | None, Depends(optional_principal)]
PrincipalDep = Annotated[PrincipalContext, Depends(required_principal)]
