"""Shared router dependencies."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.db.session import get_session
from src.schemas.identity import PrincipalContext

SessionDep = Annotated[Session, Depends(get_session)]


def optional_principal(request: Request) -> PrincipalContext | None:
    return getattr(request.state, "principal", None)


def required_principal(request: Request) -> PrincipalContext:
    principal = optional_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authenticated principal required")
    return principal


OptionalPrincipalDep = Annotated[PrincipalContext | None, Depends(optional_principal)]
PrincipalDep = Annotated[PrincipalContext, Depends(required_principal)]
