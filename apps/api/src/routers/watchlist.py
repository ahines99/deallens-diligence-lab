"""Watchlist endpoints (G19): track companies and trigger scheduled-refresh manually.

Organization scoping mirrors ``notifications.py`` / ``portfolio.py``: tenant identifiers are
non-enumerable, so reaching for another organization's watchlist returns 404, not 403.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.watchlist import (
    WatchlistEntryCreate,
    WatchlistEntryOut,
    WatchlistRefreshResult,
)
from src.services import watchlist_service as service

router = APIRouter(prefix="/api", tags=["watchlist"])


def _authorize(organization_id: str, principal) -> None:
    if principal is not None and principal.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")


@router.post(
    "/organizations/{organization_id}/watchlist",
    response_model=WatchlistEntryOut,
    status_code=201,
)
def add_watchlist_entry(
    organization_id: str,
    payload: WatchlistEntryCreate,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> WatchlistEntryOut:
    _authorize(organization_id, principal)
    try:
        entry = service.add_entry(
            session,
            organization_id,
            ticker=payload.ticker,
            cik=payload.cik,
            company_name=payload.company_name,
            created_by=principal.user_id if principal else None,
        )
    except service.WatchlistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return WatchlistEntryOut.model_validate(entry)


@router.get(
    "/organizations/{organization_id}/watchlist",
    response_model=list[WatchlistEntryOut],
)
def list_watchlist(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> list[WatchlistEntryOut]:
    _authorize(organization_id, principal)
    return [
        WatchlistEntryOut.model_validate(entry)
        for entry in service.list_entries(session, organization_id)
    ]


@router.delete("/watchlist/{entry_id}", status_code=204)
def delete_watchlist_entry(
    entry_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> None:
    removed = service.remove_entry(
        session, entry_id, principal.organization_id if principal else None
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")


@router.post(
    "/organizations/{organization_id}/watchlist/refresh",
    response_model=WatchlistRefreshResult,
)
def refresh_watchlist(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> WatchlistRefreshResult:
    _authorize(organization_id, principal)
    return WatchlistRefreshResult.model_validate(
        service.refresh_watchlist(session, organization_id)
    )


__all__ = ["router"]
