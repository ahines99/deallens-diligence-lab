"""Portfolio command-center contracts.

The dashboard intentionally returns transparent components instead of one opaque score so an IC
user can trace every headline metric back to operational records.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class DistributionPoint(BaseModel):
    key: str
    label: str
    count: int
    percent: float


class PortfolioHeadline(BaseModel):
    deals: int
    active_deals: int
    funds: int
    at_ic: int
    ic_next_30_days: int
    overdue_tasks: int
    critical_risks: int
    open_conditions: int
    average_readiness: float


class ReadinessComponent(BaseModel):
    key: str
    label: str
    score: float
    weight: float
    passed: int
    total: int
    explanation: str


class SourceHealth(BaseModel):
    status: str
    total_sources: int
    ready: int
    partial: int
    failed: int
    freshest_at: datetime | None
    oldest_age_days: int | None
    stale: bool


class FinancialQuality(BaseModel):
    mapping_coverage: float | None
    mapped_facts: int
    total_facts: int
    reconciliation_score: float | None
    reconciliations_passed: int
    reconciliations_total: int
    open_exceptions: int
    qoe_adjustment_amount: float
    qoe_materiality: float | None
    reported_ebitda: float | None
    sponsor_adjusted_ebitda: float | None
    ebitda_variance: float | None
    period_consistent: bool | None
    period_diagnostics: list[str] = Field(default_factory=list)


class DealPortfolioRow(BaseModel):
    id: str
    code: str
    name: str
    target_company: str
    fund_id: str
    fund_name: str
    strategy: str
    workspace_id: str | None
    sector: str
    stage: str
    status: str
    owner_actor_id: str | None
    ic_date: date | None
    stage_age_days: int
    readiness_score: float
    readiness_components: list[ReadinessComponent]
    source_health: SourceHealth
    financial_quality: FinancialQuality


class CalendarItem(BaseModel):
    deal_id: str
    code: str
    name: str
    ic_date: date
    days_until: int
    stage: str


class TaskQueueItem(BaseModel):
    task_id: str
    deal_id: str
    deal_code: str
    title: str
    assignee_actor_id: str | None
    priority: str
    status: str
    due_date: date
    days_overdue: int


class WorkstreamHealth(BaseModel):
    deal_id: str
    deal_code: str
    total: int
    complete: int
    in_progress: int
    blocked: int
    late: int
    health: str


class DiligenceSLAItem(BaseModel):
    request_id: str
    deal_id: str
    deal_code: str
    request_number: int
    title: str
    status: str
    priority: str
    owner_actor_id: str | None
    due_date: date | None
    age_days: int
    days_overdue: int
    sla_status: str


class RiskRegisterItem(BaseModel):
    entry_id: str
    deal_id: str
    deal_code: str
    title: str
    severity: str
    status: str
    owner_actor_id: str | None
    evidence_refs: list[str]
    age_days: int


class ConditionTrackerItem(BaseModel):
    condition_id: str
    deal_id: str
    deal_code: str
    description: str
    owner_actor_id: str | None
    due_date: date | None
    status: str
    days_overdue: int


class WorkloadItem(BaseModel):
    actor_id: str
    open_tasks: int
    overdue_tasks: int
    critical_tasks: int
    deals: int


class ReturnCase(BaseModel):
    case_key: str
    case_version_id: str
    version: int
    created_at: datetime
    moic: float | None
    xirr: float | None
    minimum_liquidity: float | None
    first_covenant_breach: str | None
    first_debt_service_default: str | None


class DealReturnsSnapshot(BaseModel):
    deal_id: str
    deal_code: str
    cases: list[ReturnCase]


class WatchlistItem(BaseModel):
    deal_id: str
    deal_code: str
    case_key: str
    reason: str
    severity: str
    metric: str
    value: float | str | None


class ImportExceptionItem(BaseModel):
    exception_id: str
    deal_id: str
    deal_code: str
    workspace_id: str
    severity: str
    code: str
    message: str
    state: str
    age_days: int


class PortfolioFilters(BaseModel):
    search: str | None
    stage: str | None
    fund_id: str | None
    as_of: date
    ic_window_days: int


class PortfolioDashboard(BaseModel):
    organization_id: str
    generated_at: datetime
    filters: PortfolioFilters
    headline: PortfolioHeadline
    stage_funnel: list[DistributionPoint]
    sector_exposure: list[DistributionPoint]
    strategy_exposure: list[DistributionPoint]
    deals: list[DealPortfolioRow]
    upcoming_ic: list[CalendarItem]
    overdue_tasks: list[TaskQueueItem]
    workstream_health: list[WorkstreamHealth]
    diligence_sla: list[DiligenceSLAItem]
    critical_risks: list[RiskRegisterItem]
    conditions_to_close: list[ConditionTrackerItem]
    team_workload: list[WorkloadItem]
    returns_snapshots: list[DealReturnsSnapshot]
    downside_watchlist: list[WatchlistItem]
    covenant_watchlist: list[WatchlistItem]
    import_exceptions: list[ImportExceptionItem]


class PortfolioHealth(BaseModel):
    organization_id: str
    generated_at: datetime
    api: str
    database: str
    sources: dict[str, int]
    stale_workspaces: int
    failed_sources: int
    partial_sources: int
    open_import_exceptions: int
    workspaces_without_sources: int


__all__ = ["PortfolioDashboard", "PortfolioHealth"]
