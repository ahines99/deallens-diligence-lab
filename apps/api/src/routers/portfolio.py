"""Portfolio command-center, export, and system-health endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.portfolio import PortfolioDashboard, PortfolioHealth
from src.services import portfolio_service as service

router = APIRouter(prefix="/api/organizations", tags=["portfolio"])


def _authorize(organization_id: str, principal) -> None:
    if principal is not None and principal.organization_id != organization_id:
        # Tenant identifiers are intentionally non-enumerable across memberships.
        raise HTTPException(status_code=404, detail="Organization not found")


def _dashboard(
    session,
    organization_id: str,
    *,
    search: str | None,
    stage: str | None,
    fund_id: str | None,
    as_of: date | None,
    ic_window_days: int,
) -> dict:
    try:
        return service.get_dashboard(
            session,
            organization_id,
            search=search,
            stage=stage,
            fund_id=fund_id,
            as_of=as_of,
            ic_window_days=ic_window_days,
        )
    except service.PortfolioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/{organization_id}/portfolio", response_model=PortfolioDashboard)
def get_portfolio_dashboard(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    search: str | None = Query(default=None, max_length=120),
    stage: str | None = Query(default=None, max_length=30),
    fund_id: str | None = Query(default=None, max_length=32),
    as_of: date | None = None,
    ic_window_days: int = Query(default=30, ge=1, le=365),
) -> PortfolioDashboard:
    _authorize(organization_id, principal)
    return PortfolioDashboard.model_validate(
        _dashboard(
            session,
            organization_id,
            search=search,
            stage=stage,
            fund_id=fund_id,
            as_of=as_of,
            ic_window_days=ic_window_days,
        )
    )


@router.get("/{organization_id}/portfolio/export.csv")
def export_portfolio_dashboard(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    search: str | None = Query(default=None, max_length=120),
    stage: str | None = Query(default=None, max_length=30),
    fund_id: str | None = Query(default=None, max_length=32),
    as_of: date | None = None,
) -> Response:
    _authorize(organization_id, principal)
    dashboard = _dashboard(
        session,
        organization_id,
        search=search,
        stage=stage,
        fund_id=fund_id,
        as_of=as_of,
        ic_window_days=30,
    )
    return Response(
        service.export_dashboard_csv(dashboard),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="portfolio-{organization_id}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{organization_id}/portfolio/health", response_model=PortfolioHealth)
def get_portfolio_health(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> PortfolioHealth:
    _authorize(organization_id, principal)
    try:
        result = service.get_health(session, organization_id)
    except service.PortfolioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return PortfolioHealth.model_validate(result)


__all__ = ["router"]
