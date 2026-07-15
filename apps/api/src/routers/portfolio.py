"""Portfolio command-center, export, and system-health endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.portfolio import (
    FundConstructionReport,
    PortfolioDashboard,
    PortfolioHealth,
)
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


@router.get("/{organization_id}/fund-construction", response_model=FundConstructionReport)
def get_fund_construction(
    organization_id: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    fund_id: str | None = Query(default=None, max_length=32),
    as_of: date | None = None,
    single_sector_max: float | None = Query(default=None, ge=0.0, le=1.0),
    single_deal_max: float | None = Query(default=None, ge=0.0, le=1.0),
    single_strategy_max: float | None = Query(default=None, ge=0.0, le=1.0),
    near_breach_ratio: float = Query(default=0.90, ge=0.0, le=1.0),
    target_fund_size: float | None = Query(default=None, ge=0.0),
    investment_period_years: int = Query(default=5, ge=1, le=30),
    pacing_tolerance: float = Query(default=0.10, ge=0.0, le=1.0),
) -> FundConstructionReport:
    _authorize(organization_id, principal)
    try:
        result = service.get_fund_construction(
            session,
            organization_id,
            fund_id=fund_id,
            as_of=as_of,
            single_sector_max=single_sector_max,
            single_deal_max=single_deal_max,
            single_strategy_max=single_strategy_max,
            near_breach_ratio=near_breach_ratio,
            target_fund_size=target_fund_size,
            investment_period_years=investment_period_years,
            pacing_tolerance=pacing_tolerance,
        )
    except service.PortfolioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return FundConstructionReport.model_validate(result)


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
