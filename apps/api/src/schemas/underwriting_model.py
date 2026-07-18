"""Contracts for versioned private-equity underwriting models.

Amounts are expressed in the case currency and rates are decimals (8% = ``0.08``).
Projection growth rates are annualized; each period declares its own month count so callers can
mix monthly, quarterly, and annual periods without changing the calculation contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CaseKey = Literal["base", "upside", "downside"]
TrancheType = Literal["revolver", "term_loan", "second_lien", "mezzanine", "seller_note"]
CovenantMetric = Literal[
    "total_leverage",
    "senior_leverage",
    "interest_coverage",
    "fixed_charge_coverage",
    "minimum_liquidity",
]
SensitivityVariable = Literal[
    "entry_multiple",
    "exit_multiple",
    "base_rate_shift",
    "revenue_growth_shift",
    "ebitda_margin_shift",
]
StressObjective = Literal["irr", "moic", "minimum_liquidity"]


class HistoricalFinancials(BaseModel):
    ltm_revenue: float = Field(gt=0)
    ltm_ebitda: float
    starting_cash: float = Field(default=0.0, ge=0)
    starting_net_working_capital: float = 0.0
    existing_debt: float = Field(default=0.0, ge=0)


class OperatingDrivers(BaseModel):
    annual_revenue_growth: float = Field(default=0.08, gt=-1.0, le=5.0)
    gross_margin: float = Field(default=0.60, ge=-2.0, le=2.0)
    ebitda_margin: float = Field(default=0.20, ge=-2.0, le=2.0)
    da_percent_revenue: float = Field(default=0.03, ge=0, le=1.0)
    capex_percent_revenue: float = Field(default=0.04, ge=-1.0, le=2.0)
    net_working_capital_percent_revenue: float = Field(default=0.10, ge=-2.0, le=2.0)
    cash_tax_rate: float = Field(default=0.25, ge=0, le=1.0)
    base_rate: float = Field(default=0.04, ge=-0.05, le=1.0)


class OperatingPeriodAssumption(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    months: int = Field(ge=1, le=120)
    annual_revenue_growth: float | None = Field(default=None, gt=-1.0, le=5.0)
    gross_margin: float | None = Field(default=None, ge=-2.0, le=2.0)
    ebitda_margin: float | None = Field(default=None, ge=-2.0, le=2.0)
    da_percent_revenue: float | None = Field(default=None, ge=0, le=1.0)
    capex_percent_revenue: float | None = Field(default=None, ge=-1.0, le=2.0)
    net_working_capital_percent_revenue: float | None = Field(default=None, ge=-2.0, le=2.0)
    cash_tax_rate: float | None = Field(default=None, ge=0, le=1.0)
    base_rate: float | None = Field(default=None, ge=-0.05, le=1.0)


class ProjectionAssumptions(BaseModel):
    default_drivers: OperatingDrivers = Field(default_factory=OperatingDrivers)
    periods: list[OperatingPeriodAssumption] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def unique_period_labels(self):
        labels = [period.label for period in self.periods]
        if len(labels) != len(set(labels)):
            raise ValueError("Projection period labels must be unique")
        return self


class DebtTrancheAssumption(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tranche_type: TrancheType
    initial_amount: float = Field(default=0.0, ge=0)
    commitment: float | None = Field(default=None, ge=0)
    senior: bool = True
    spread: float = Field(default=0.0, ge=0, le=1.0)
    base_rate_floor: float = Field(default=0.0, ge=-0.05, le=1.0)
    pik_rate: float = Field(default=0.0, ge=0, le=1.0)
    annual_amortization_rate: float = Field(default=0.0, ge=0, le=1.0)
    cash_sweep_priority: int = Field(default=100, ge=0, le=10_000)
    sweep_eligible: bool = True
    maturity_period: str | None = Field(default=None, max_length=40)
    oid_discount: float = Field(default=0.0, ge=0, lt=1.0)
    financing_fee_percent: float = Field(default=0.0, ge=0, lt=1.0)

    @model_validator(mode="after")
    def validate_revolver_commitment(self):
        if self.tranche_type == "revolver":
            if self.commitment is None:
                self.commitment = self.initial_amount
            if self.commitment < self.initial_amount:
                raise ValueError("Revolver commitment cannot be below its initial draw")
        return self


class CovenantAssumption(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    metric: CovenantMetric
    test: Literal["maximum", "minimum"]
    threshold: float
    threshold_by_period: dict[str, float] = Field(default_factory=dict)


class TransactionAssumptions(BaseModel):
    close_date: date
    entry_multiple: float = Field(gt=0, le=100)
    exit_multiple: float = Field(gt=0, le=100)
    hold_period_years: float = Field(default=5.0, gt=0, le=30)
    transaction_fees: float = Field(default=0.0, ge=0)
    management_options_cashout: float = Field(default=0.0, ge=0)
    other_uses: float = Field(default=0.0, ge=0)
    seller_rollover: float = Field(default=0.0, ge=0)
    minimum_cash: float = Field(default=0.0, ge=0)
    cash_sweep_percent: float = Field(default=1.0, ge=0, le=1.0)


class ValuationAssumptions(BaseModel):
    discount_rate: float = Field(default=0.10, gt=-0.99, le=2.0)
    terminal_growth_rate: float = Field(default=0.025, gt=-1.0, le=1.0)
    mid_year_convention: bool = True

    @model_validator(mode="after")
    def valid_gordon_spread(self):
        if self.discount_rate <= self.terminal_growth_rate:
            raise ValueError("Discount rate must exceed terminal growth rate")
        return self


class UnderwritingAssumptions(BaseModel):
    currency: str = Field(default="USD", min_length=3, max_length=3)
    historical: HistoricalFinancials
    transaction: TransactionAssumptions
    projection: ProjectionAssumptions = Field(default_factory=ProjectionAssumptions)
    debt_tranches: list[DebtTrancheAssumption] = Field(default_factory=list, max_length=30)
    covenants: list[CovenantAssumption] = Field(default_factory=list, max_length=50)
    valuation: ValuationAssumptions = Field(default_factory=ValuationAssumptions)

    @model_validator(mode="after")
    def validate_model(self):
        names = [tranche.name for tranche in self.debt_tranches]
        if len(names) != len(set(names)):
            raise ValueError("Debt tranche names must be unique")
        default_drivers = self.projection.default_drivers
        if default_drivers.ebitda_margin > default_drivers.gross_margin:
            raise ValueError("Default EBITDA margin cannot exceed gross margin")
        for period in self.projection.periods:
            gross_margin = (
                period.gross_margin
                if period.gross_margin is not None
                else default_drivers.gross_margin
            )
            ebitda_margin = (
                period.ebitda_margin
                if period.ebitda_margin is not None
                else default_drivers.ebitda_margin
            )
            if ebitda_margin > gross_margin:
                raise ValueError(f"EBITDA margin cannot exceed gross margin in {period.label}")
        if self.projection.periods:
            modeled_months = sum(period.months for period in self.projection.periods)
            expected_months = round(self.transaction.hold_period_years * 12)
            if modeled_months != expected_months:
                raise ValueError(
                    "Projection periods must span the transaction hold period "
                    f"({modeled_months} modeled months vs. {expected_months} expected)"
                )
        period_labels = {period.label for period in self.projection.periods}
        if period_labels:
            unknown_maturities = {
                tranche.maturity_period
                for tranche in self.debt_tranches
                if tranche.maturity_period and tranche.maturity_period not in period_labels
            }
            if unknown_maturities:
                raise ValueError(
                    "Debt maturity periods are absent from the projection: "
                    + ", ".join(sorted(unknown_maturities))
                )
        self.currency = self.currency.upper()
        return self


class SourceUseLine(BaseModel):
    name: str
    amount: float


class SourcesUsesResult(BaseModel):
    entry_enterprise_value: float
    equity_purchase_price: float
    uses: list[SourceUseLine]
    sources: list[SourceUseLine]
    total_uses: float
    total_sources: float
    sponsor_equity: float
    rollover_equity: float
    sponsor_ownership: float
    balanced: bool


class DebtTranchePeriodResult(BaseModel):
    name: str
    tranche_type: TrancheType
    opening_balance: float
    cash_rate: float
    cash_interest: float
    pik_interest: float
    required_amortization: float
    paid_amortization: float
    revolver_draw: float
    cash_sweep: float
    unpaid_amortization: float
    ending_balance: float


class CovenantPeriodResult(BaseModel):
    name: str
    metric: CovenantMetric
    test: Literal["maximum", "minimum"]
    actual: float | None
    threshold: float
    headroom: float | None
    passed: bool | None


class ProjectionPeriodResult(BaseModel):
    label: str
    start_date: date
    end_date: date
    months: int
    year_fraction: float
    revenue: float
    annualized_revenue: float
    revenue_growth: float
    cost_of_goods_sold: float
    gross_profit: float
    operating_expenses: float
    ebitda: float
    ebitda_margin: float
    depreciation_amortization: float
    ebit: float
    cash_interest: float
    pik_interest: float
    earnings_before_tax: float
    cash_taxes: float
    net_income: float
    net_working_capital: float
    change_in_net_working_capital: float
    capex: float
    fcff: float
    beginning_cash: float
    cash_before_debt_service: float
    revolver_draw: float
    mandatory_amortization: float
    cash_sweep: float
    # G70 seam: an equity distribution paid through this period's cash waterfall. Always 0.0 in
    # the base engine; populated only when a caller threads ``special_distributions`` through
    # ``calculate_projection`` (the dividend-recap solver).
    special_distribution: float = 0.0
    ending_cash: float
    liquidity_shortfall: float
    total_debt: float
    net_debt: float
    total_leverage: float | None
    senior_leverage: float | None
    interest_coverage: float | None
    fixed_charge_coverage: float | None
    liquidity: float
    debt_tranches: list[DebtTranchePeriodResult]
    covenants: list[CovenantPeriodResult]


class DcfResult(BaseModel):
    discount_rate: float
    terminal_growth_rate: float
    pv_explicit_fcff: float
    terminal_value: float
    pv_terminal_value: float
    enterprise_value: float
    net_debt: float
    equity_value: float
    terminal_value_percent: float | None


class ReturnsResult(BaseModel):
    exit_enterprise_value: float
    exit_debt: float
    exit_cash: float
    exit_equity_value: float
    sponsor_exit_proceeds: float
    sponsor_invested_capital: float
    moic: float | None
    xirr: float | None
    cash_flows: list[dict]


class UnderwritingSummary(BaseModel):
    revenue_cagr: float | None
    exit_ebitda: float
    exit_ebitda_margin: float
    minimum_liquidity: float
    maximum_total_leverage: float | None
    first_covenant_breach: str | None
    first_debt_service_default: str | None


class UnderwritingResult(BaseModel):
    currency: str
    sources_uses: SourcesUsesResult
    projection: list[ProjectionPeriodResult]
    dcf: DcfResult
    returns: ReturnsResult
    summary: UnderwritingSummary
    generated_at: datetime


class UnderwritingCalculateRequest(BaseModel):
    assumptions: UnderwritingAssumptions


class UnderwritingCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: CaseKey
    label: str = Field(default="", max_length=160)
    assumptions: UnderwritingAssumptions
    approved_claim_ids: list[str] = Field(default_factory=list, max_length=2_000)
    expected_parent_version: int | None = Field(default=None, ge=1)
    created_by: str = Field(default="system", min_length=1, max_length=120)
    change_note: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def unique_approved_claims(self):
        if len(self.approved_claim_ids) != len(set(self.approved_claim_ids)):
            raise ValueError("approved_claim_ids must be unique")
        return self


class UnderwritingCaseSetCreate(BaseModel):
    cases: list[UnderwritingCaseCreate] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def exactly_three_cases(self):
        keys = {case.case_key for case in self.cases}
        if keys != {"base", "upside", "downside"}:
            raise ValueError("A case set must contain exactly base, upside, and downside")
        return self


class UnderwritingDecisionCreate(BaseModel):
    decision: Literal["submitted", "approved", "rejected", "superseded"]
    actor: str = Field(min_length=1, max_length=120)
    rationale: str = Field(default="", max_length=4_000)


class UnderwritingDecisionOut(BaseModel):
    id: str
    decision: str
    actor: str
    rationale: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnderwritingCaseVersionOut(BaseModel):
    id: str
    workspace_id: str
    case_key: CaseKey
    label: str
    version: int
    parent_version_id: str | None
    schema_version: str
    assumptions: UnderwritingAssumptions
    result: UnderwritingResult
    approved_claim_ids: list[str]
    approved_claim_manifest: list[dict]
    claim_manifest_hash: str
    input_hash: str
    output_hash: str
    created_by: str
    change_note: str
    created_at: datetime
    latest_decision: UnderwritingDecisionOut | None = None


class SensitivityAxis(BaseModel):
    variable: SensitivityVariable
    values: list[float] = Field(min_length=2, max_length=15)


class SensitivityRequest(BaseModel):
    assumptions: UnderwritingAssumptions
    rows: SensitivityAxis
    columns: SensitivityAxis
    metric: Literal["irr", "moic", "minimum_liquidity"] = "irr"

    @model_validator(mode="after")
    def different_axes(self):
        if self.rows.variable == self.columns.variable:
            raise ValueError("Sensitivity row and column variables must differ")
        return self


class SensitivityResult(BaseModel):
    row_variable: SensitivityVariable
    row_values: list[float]
    column_variable: SensitivityVariable
    column_values: list[float]
    metric: str
    grid: list[list[float | None]]


class ReverseStressRequest(BaseModel):
    assumptions: UnderwritingAssumptions
    variable: SensitivityVariable
    objective: StressObjective = "irr"
    target: float
    lower_bound: float
    upper_bound: float
    tolerance: float = Field(default=1e-5, gt=0, le=0.1)
    max_iterations: int = Field(default=80, ge=1, le=500)

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.lower_bound >= self.upper_bound:
            raise ValueError("Reverse-stress lower_bound must be below upper_bound")
        return self


class ReverseStressResult(BaseModel):
    status: Literal["solved", "no_solution"]
    variable: SensitivityVariable
    objective: StressObjective
    target: float
    solved_value: float | None
    achieved_value: float | None
    lower_value: float | None
    upper_value: float | None
    iterations: int


class WorkingCapitalObservation(BaseModel):
    observation_date: date
    accounts_receivable: float = 0.0
    inventory: float = 0.0
    other_operating_current_assets: float = 0.0
    accounts_payable: float = 0.0
    accrued_liabilities: float = 0.0
    deferred_revenue: float = 0.0
    other_operating_current_liabilities: float = 0.0
    excluded_net_amount: float = 0.0


class WorkingCapitalPegRequest(BaseModel):
    observations: list[WorkingCapitalObservation] = Field(min_length=1, max_length=120)
    closing_date: date
    method: Literal["median_ltm", "average_ltm", "seasonal_average"] = "median_ltm"
    delivered_working_capital: float | None = None


class NormalizedWorkingCapital(BaseModel):
    observation_date: date
    normalized_working_capital: float


class WorkingCapitalPegResult(BaseModel):
    method: str
    peg: float
    trailing_average: float
    trailing_median: float
    low: float
    high: float
    seasonal_month: int
    seasonal_average: float | None
    delivered_working_capital: float | None
    purchase_price_adjustment: float | None
    observations: list[NormalizedWorkingCapital]


class ValuationReference(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    ev_ebitda_multiple: float = Field(gt=0, le=100)
    source: str = Field(min_length=1, max_length=240)
    as_of_date: date | None = None
    evidence_ref: str | None = Field(default=None, max_length=40)


class ValuationTriangulationRequest(BaseModel):
    ebitda: float = Field(gt=0)
    net_debt: float = 0.0
    dcf_enterprise_value: float | None = Field(default=None, gt=0)
    public_comps: list[ValuationReference] = Field(default_factory=list, max_length=50)
    precedent_transactions: list[ValuationReference] = Field(default_factory=list, max_length=50)
    dcf_weight: float = Field(default=0.40, ge=0, le=1)
    public_comps_weight: float = Field(default=0.35, ge=0, le=1)
    precedents_weight: float = Field(default=0.25, ge=0, le=1)

    @model_validator(mode="after")
    def at_least_one_method(self):
        if not self.dcf_enterprise_value and not self.public_comps and not self.precedent_transactions:
            raise ValueError("At least one valuation method is required")
        return self


class ValuationMethodResult(BaseModel):
    method: Literal["dcf", "public_comps", "precedent_transactions"]
    reference_count: int
    multiple_low: float | None
    multiple_median: float | None
    multiple_high: float | None
    enterprise_value_low: float
    enterprise_value_median: float
    enterprise_value_high: float
    requested_weight: float
    normalized_weight: float


class ValuationTriangulationResult(BaseModel):
    ebitda: float
    net_debt: float
    methods: list[ValuationMethodResult]
    blended_enterprise_value: float
    blended_equity_value: float
    valuation_low: float
    valuation_high: float
    warnings: list[str]


DistributionKind = Literal["normal", "uniform", "triangular"]


class DistributionParameters(BaseModel):
    """Validated sampling parameters shared by Monte Carlo drivers and fund macro factors.

    ``normal`` requires ``mean``/``std_dev``; ``uniform`` requires ``low``/``high``;
    ``triangular`` requires ``low``/``mode``/``high``.
    """

    kind: DistributionKind
    mean: float | None = None
    std_dev: float | None = Field(default=None, ge=0)
    low: float | None = None
    mode: float | None = None
    high: float | None = None

    @model_validator(mode="after")
    def validate_parameters(self):
        if self.kind == "normal":
            if self.mean is None or self.std_dev is None:
                raise ValueError("Normal distributions require mean and std_dev")
        elif self.kind == "uniform":
            if self.low is None or self.high is None:
                raise ValueError("Uniform distributions require low and high")
            if self.low > self.high:
                raise ValueError("Uniform low cannot exceed high")
        else:
            if self.low is None or self.mode is None or self.high is None:
                raise ValueError("Triangular distributions require low, mode, and high")
            if not self.low <= self.mode <= self.high:
                raise ValueError("Triangular distributions require low <= mode <= high")
        return self


class DriverDistribution(DistributionParameters):
    """Sampling specification for one Monte Carlo driver.

    Drivers reuse the sensitivity variables: ``entry_multiple`` and ``exit_multiple`` are sampled
    as absolute EV/EBITDA turns, while ``base_rate_shift``, ``revenue_growth_shift``, and
    ``ebitda_margin_shift`` are sampled as additive shifts on the deterministic assumptions
    (``0.01`` = +100 bps).
    """

    driver: SensitivityVariable


class MonteCarloRequest(BaseModel):
    assumptions: UnderwritingAssumptions
    iterations: int = Field(default=1_000, ge=100, le=5_000)
    seed: int = 42
    distributions: list[DriverDistribution] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_drivers(self):
        drivers = [distribution.driver for distribution in self.distributions]
        if len(drivers) != len(set(drivers)):
            raise ValueError("Each Monte Carlo driver may appear at most once")
        return self


class MetricPercentileBand(BaseModel):
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float
    mean: float


class MonteCarloDriverSummary(BaseModel):
    driver: SensitivityVariable
    kind: DistributionKind
    sampled_mean: float
    sampled_min: float
    sampled_max: float


class MonteCarloResult(BaseModel):
    iterations: int
    seed: int
    converged: int
    failed: int
    irr: MetricPercentileBand
    moic: MetricPercentileBand
    probability_irr_below_zero: float
    probability_moic_below_1: float
    driver_summaries: list[MonteCarloDriverSummary]


AttributionComponentKey = Literal[
    "ebitda_growth",
    "multiple_change",
    "deleveraging",
    "cross_term",
]


class ReturnsAttributionRequest(BaseModel):
    assumptions: UnderwritingAssumptions


class AttributionComponent(BaseModel):
    key: AttributionComponentKey
    label: str
    amount: float
    share_of_total: float | None


class ReturnsAttributionResult(BaseModel):
    entry_multiple: float
    entry_ebitda: float
    entry_net_debt: float
    entry_equity: float
    exit_multiple: float
    exit_ebitda: float
    exit_net_debt: float
    exit_equity: float
    total_value_creation: float
    components: list[AttributionComponent]
    reconciles: bool


# --- G23 Covenant headroom projection ---------------------------------------------------------


class CovenantHeadroomPeriod(BaseModel):
    period_label: str
    start_date: date
    end_date: date
    actual: float | None
    threshold: float
    headroom: float | None
    breached: bool


class CovenantHeadroomProjection(BaseModel):
    name: str
    metric: CovenantMetric
    test: Literal["maximum", "minimum"]
    periods: list[CovenantHeadroomPeriod]
    first_breach_period: str | None
    breached: bool


class CovenantHeadroomResult(BaseModel):
    currency: str
    covenants: list[CovenantHeadroomProjection]
    generated_at: datetime


# --- G27 Management-vs-sponsor case variance --------------------------------------------------


class CaseVarianceOperand(BaseModel):
    """One side of a variance comparison: inline assumptions or a persisted case reference."""

    assumptions: UnderwritingAssumptions | None = None
    case_key: CaseKey | None = None
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.assumptions is None) == (self.case_key is None):
            raise ValueError("Provide exactly one of assumptions or case_key")
        if self.assumptions is not None and self.version is not None:
            raise ValueError("version applies only when comparing a persisted case_key")
        return self


class CaseVarianceRequest(BaseModel):
    management: CaseVarianceOperand
    sponsor: CaseVarianceOperand


class CaseVarianceLine(BaseModel):
    key: str
    label: str
    management_value: float | None
    sponsor_value: float | None
    absolute_delta: float | None
    pct_delta: float | None
    materiality_rank: int


class CaseVarianceResult(BaseModel):
    management_label: str
    sponsor_label: str
    lines: list[CaseVarianceLine]
    generated_at: datetime


# --- G28 Exit readiness scorecard + hold-period sensitivity -----------------------------------


class ExitReadinessDimension(BaseModel):
    dimension: str
    metric: str
    value: float | None
    threshold: float
    direction: Literal["higher_is_better", "lower_is_better"]
    meets_threshold: bool | None
    score: float
    rating: str


class HoldPeriodPoint(BaseModel):
    hold_period_years: float
    irr: float | None
    moic: float | None
    exit_ebitda: float
    exit_equity_value: float


class ExitReadinessResult(BaseModel):
    dimensions: list[ExitReadinessDimension]
    overall_score: float
    overall_rating: str
    hold_period_grid: list[HoldPeriodPoint]
    generated_at: datetime


# --- G30 Valuation football field -------------------------------------------------------------


class FootballFieldMethod(BaseModel):
    method: Literal["dcf", "public_comps", "precedent_transactions"]
    label: str
    reference_count: int
    low: float | None
    mid: float | None
    high: float | None
    weight: float
    included: bool
    excluded_reason: str | None


class FootballFieldResult(BaseModel):
    ebitda: float
    net_debt: float
    methods: list[FootballFieldMethod]
    included_weight_total: float
    blended_enterprise_value: float
    blended_equity_value: float
    valuation_low: float
    valuation_high: float
    warnings: list[str]
    generated_at: datetime


# --- G24 Driver-based operating model ---------------------------------------------------------


class DriverDefinition(BaseModel):
    """One user-defined driver. ``formula`` is the right-hand-side expression only.

    A leaf driver's formula is a numeric constant (e.g. ``"100"``); a derived driver references
    other driver names and constants with ``+ - * /`` and parentheses (e.g. ``"units * price"``).
    """

    name: str = Field(min_length=1, max_length=60)
    formula: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=40)
    provenance: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def name_is_referenceable(self):
        if not self.name.isidentifier():
            raise ValueError(
                f"Driver name '{self.name}' must be a valid identifier so formulas can reference it"
            )
        return self


class DriverModelRequest(BaseModel):
    drivers: list[DriverDefinition] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_driver_names(self):
        names = [driver.name for driver in self.drivers]
        if len(names) != len(set(names)):
            raise ValueError("Driver names must be unique")
        return self


class DriverProvenance(BaseModel):
    note: str | None
    inputs: list[str]


class ResolvedDriver(BaseModel):
    name: str
    value: float
    formula: str
    unit: str | None
    depends_on: list[str]
    provenance: DriverProvenance


class DriverModelResult(BaseModel):
    resolved: list[ResolvedDriver]
    evaluation_order: list[str]


# --- G25 Working-capital seasonality ----------------------------------------------------------


class MonthlyWorkingCapital(BaseModel):
    month: int = Field(ge=1, le=12)
    value: float


class WorkingCapitalSeasonalityRequest(BaseModel):
    monthly_working_capital: list[MonthlyWorkingCapital] = Field(min_length=1, max_length=120)


class SeasonalMonthPeg(BaseModel):
    month: int
    peg: float
    observation_count: int


class WorkingCapitalSeasonalityResult(BaseModel):
    status: Literal["complete", "partial"]
    monthly_pegs: list[SeasonalMonthPeg]
    present_months: list[int]
    missing_months: list[int]
    annual_average: float
    peak_month: int
    trough_month: int
    amplitude: float


# --- G26 Dividend recap + bolt-on acquisition events -------------------------------------------


class RecapBoltOnEvent(BaseModel):
    """A capital event applied at a projection period.

    ``dividend_recap`` draws incremental debt (``amount``) to fund an equity dividend.
    ``bolt_on`` acquires ``incremental_ebitda`` at ``multiple_paid``, funded by ``funded_by``.
    """

    type: Literal["dividend_recap", "bolt_on"]
    period: str = Field(min_length=1, max_length=40)
    amount: float | None = Field(default=None)
    incremental_ebitda: float | None = Field(default=None)
    multiple_paid: float | None = Field(default=None, gt=0, le=100)
    funded_by: Literal["debt", "equity"] = "debt"

    @model_validator(mode="after")
    def validate_event(self):
        if self.type == "dividend_recap":
            if self.amount is None or self.amount <= 0:
                raise ValueError("dividend_recap requires a positive amount")
        else:
            if self.incremental_ebitda is None or self.multiple_paid is None:
                raise ValueError("bolt_on requires incremental_ebitda and multiple_paid")
            if self.incremental_ebitda <= 0:
                raise ValueError("bolt_on incremental_ebitda must be positive")
        return self


class RecapBoltOnRequest(BaseModel):
    assumptions: UnderwritingAssumptions
    events: list[RecapBoltOnEvent] = Field(min_length=1, max_length=20)


class EventSourcesUses(BaseModel):
    type: str
    period: str
    sources: list[SourceUseLine]
    uses: list[SourceUseLine]
    balanced: bool


class RecapBoltOnReturns(BaseModel):
    irr: float | None
    moic: float | None
    exit_debt: float
    exit_ebitda: float
    exit_equity_value: float
    exit_leverage: float | None
    sponsor_exit_proceeds: float
    sponsor_invested_capital: float
    cash_flows: list[dict]


class RecapBoltOnResult(BaseModel):
    base: RecapBoltOnReturns
    adjusted: RecapBoltOnReturns
    events: list[EventSourcesUses]
    irr_delta: float | None
    moic_delta: float | None
    leverage_delta: float | None
    sources_uses_balanced: bool
    generated_at: datetime


# --- G69 One-way sensitivity tornado ----------------------------------------------------------


TornadoMetric = Literal["irr", "moic"]
TornadoConvention = Literal["relative", "absolute"]


class SensitivityTornadoRequest(BaseModel):
    """One-way tornado over the sensitivity-variable vocabulary.

    Shift conventions per variable (see ``_TORNADO_CONVENTIONS`` in the service):

    - ``entry_multiple`` / ``exit_multiple`` are level variables, shifted RELATIVELY:
      low/high = base multiple x (1 -/+ ``relative_shift``).
    - ``base_rate_shift`` / ``revenue_growth_shift`` / ``ebitda_margin_shift`` are additive
      shifts around a base of zero, where a relative shift of a zero base is meaningless; they
      move ABSOLUTELY by -/+ ``absolute_shift`` (``0.01`` = 100 bps).
    """

    assumptions: UnderwritingAssumptions
    metric: TornadoMetric = "irr"
    relative_shift: float = Field(default=0.10, gt=0, le=0.9)
    absolute_shift: float = Field(default=0.01, gt=0, le=0.5)
    variables: list[SensitivityVariable] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def unique_variables(self):
        if self.variables is not None and len(self.variables) != len(set(self.variables)):
            raise ValueError("Tornado variables must be unique")
        return self


class TornadoRow(BaseModel):
    variable: SensitivityVariable
    convention: TornadoConvention
    base_value: float
    low_value: float
    high_value: float
    metric_low: float | None
    metric_high: float | None
    delta_low: float | None
    delta_high: float | None
    max_abs_delta: float | None
    evaluable: bool
    reason: str | None


class SensitivityTornadoResult(BaseModel):
    metric: TornadoMetric
    base_metric: float
    relative_shift: float
    absolute_shift: float
    rows: list[TornadoRow]


# --- G70 Dividend recap solver ----------------------------------------------------------------


RecapConstraintName = Literal[
    "max_total_leverage",
    "min_interest_coverage",
    "min_fixed_charge_coverage",
    "min_liquidity",
]


class DividendRecapSolveRequest(BaseModel):
    """Solve the maximum special distribution at the end of ``period`` by bisection.

    The distribution flows through that period's cash waterfall (the committed revolver may fund
    it), and every constraint is tested at the distribution period and every LATER period — the
    horizon the distribution can affect; earlier periods are unchanged by construction. At least
    one constraint is required.
    """

    assumptions: UnderwritingAssumptions
    period: str = Field(min_length=1, max_length=40)
    max_total_leverage: float | None = Field(default=None, gt=0)
    min_interest_coverage: float | None = Field(default=None, gt=0)
    min_fixed_charge_coverage: float | None = Field(default=None, gt=0)
    min_liquidity: float | None = None
    tolerance: float = Field(default=0.01, gt=0, le=1_000_000)
    max_iterations: int = Field(default=80, ge=1, le=500)

    @model_validator(mode="after")
    def at_least_one_constraint(self):
        if (
            self.max_total_leverage is None
            and self.min_interest_coverage is None
            and self.min_fixed_charge_coverage is None
            and self.min_liquidity is None
        ):
            raise ValueError("At least one recap constraint is required")
        return self


class RecapConstraintStatus(BaseModel):
    name: RecapConstraintName
    threshold: float
    actual: float | None
    binding_period: str | None
    headroom: float | None
    satisfied: bool
    note: str | None


class DividendRecapSolveResult(BaseModel):
    status: Literal["solved", "infeasible", "unbounded"]
    period: str
    max_distribution: float | None
    sponsor_share: float | None
    binding_constraint: RecapConstraintName | None
    constraints: list[RecapConstraintStatus]
    iterations: int
    note: str | None


# --- G71 Working-capital facility sizing ------------------------------------------------------


class FacilitySizingRequest(BaseModel):
    """Size the revolver against intra-year working-capital seasonality (G25 model).

    With no monthly observations the result is an explicit ``unavailable`` — a flat monthly
    profile is never fabricated. ``commitment_override`` replaces the modeled revolver
    commitments when provided.
    """

    assumptions: UnderwritingAssumptions
    monthly_working_capital: list[MonthlyWorkingCapital] = Field(
        default_factory=list, max_length=120
    )
    commitment_override: float | None = Field(default=None, ge=0)


class FacilityYearSizing(BaseModel):
    year_label: str
    period_label: str
    months: int
    annual_nwc: float
    evaluable: bool
    reason: str | None
    peak_month: int | None
    peak_monthly_nwc: float | None
    peak_draw: float | None
    existing_revolver_draw: float | None
    commitment: float
    headroom: float | None


class FacilitySizingResult(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None
    seasonality_missing_months: list[int]
    seasonal_annual_average: float | None
    seasonal_peak_month: int | None
    commitment: float
    commitment_source: Literal["modeled_revolvers", "override"]
    years: list[FacilityYearSizing]
    peak_year_label: str | None
    peak_draw: float | None


# --- G72 Fund-level Monte Carlo ---------------------------------------------------------------


FundFactorName = Literal["rate_shift", "growth_shift", "multiple_shift"]


class FundFactorSpec(DistributionParameters):
    """A macro factor sampled ONCE per iteration and shared by every deal.

    Factor-to-driver mapping (applied via ``_apply_variable`` with the deal's loading):

    - ``rate_shift``     -> ``base_rate_shift`` (additive rate shift, ``0.01`` = 100 bps)
    - ``growth_shift``   -> ``revenue_growth_shift`` (additive growth shift)
    - ``multiple_shift`` -> ``exit_multiple`` (additive EV/EBITDA turns added to the deal's
      exit multiple)
    """

    name: FundFactorName


class FundDealSpec(BaseModel):
    """One deal in the fund simulation.

    ``commitment`` defaults to the deal's own sponsor equity; when it differs, the deal's
    sponsor cash flows are scaled by ``commitment / sponsor_equity`` in the pooled fund IRR.
    ``loadings`` maps factor name -> sensitivity (default 1.0 for every requested factor; 0
    decouples the deal from that factor). ``distributions`` are the deal's idiosyncratic draws,
    sampled independently per deal per iteration with G21 semantics.
    """

    name: str = Field(min_length=1, max_length=120)
    assumptions: UnderwritingAssumptions
    commitment: float | None = Field(default=None, gt=0)
    loadings: dict[FundFactorName, float] = Field(default_factory=dict)
    distributions: list[DriverDistribution] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_idiosyncratic_drivers(self):
        drivers = [distribution.driver for distribution in self.distributions]
        if len(drivers) != len(set(drivers)):
            raise ValueError("Each idiosyncratic driver may appear at most once per deal")
        return self


class FundMonteCarloRequest(BaseModel):
    """Fund-level Monte Carlo over shared macro factor draws plus per-deal idiosyncratic draws.

    Provide EITHER inline ``deals`` OR a ``fund_id`` whose saved fund-construction sizing cases
    (base-case preference, committed sponsor equity — G29 discipline, never imputed) become the
    deal set; ``fund_deal_loadings`` applies to every fund-resolved deal (default 1.0).
    """

    deals: list[FundDealSpec] = Field(default_factory=list, max_length=20)
    fund_id: str | None = Field(default=None, max_length=64)
    fund_deal_loadings: dict[FundFactorName, float] = Field(default_factory=dict)
    iterations: int = Field(default=500, ge=100, le=2_000)
    seed: int = 42
    factors: list[FundFactorSpec] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_sources_and_uniqueness(self):
        if bool(self.deals) == (self.fund_id is not None):
            raise ValueError("Provide exactly one of deals or fund_id")
        names = [deal.name for deal in self.deals]
        if len(names) != len(set(names)):
            raise ValueError("Fund deal names must be unique")
        factor_names = [factor.name for factor in self.factors]
        if len(factor_names) != len(set(factor_names)):
            raise ValueError("Each macro factor may appear at most once")
        return self


class ExcludedFundDeal(BaseModel):
    code: str
    reason: str


class FundFactorSummary(BaseModel):
    name: FundFactorName
    kind: DistributionKind
    sampled_mean: float
    sampled_min: float
    sampled_max: float


class FundDealOutcome(BaseModel):
    name: str
    commitment: float
    base_invested: float
    irr: MetricPercentileBand
    moic: MetricPercentileBand
    probability_moic_below_1: float


class FundCorrelationEffect(BaseModel):
    """The SAME seed and draws re-run with every factor loading zeroed (independent deals).

    Negative p5 spreads (correlated minus independent) and positive p95 spreads show the shared
    macro factors widening the fund outcome distribution versus independent deals.
    """

    independent_converged: int
    independent_failed: int
    independent_irr: MetricPercentileBand
    independent_moic: MetricPercentileBand
    independent_probability_fund_moic_below_1: float
    irr_p5_spread: float
    irr_p95_spread: float
    moic_p5_spread: float
    moic_p95_spread: float
    note: str


class FundMonteCarloResult(BaseModel):
    iterations: int
    seed: int
    converged: int
    failed: int
    source: Literal["request", "fund_construction"]
    fund_id: str | None
    excluded_deals: list[ExcludedFundDeal]
    total_commitment: float
    fund_irr: MetricPercentileBand
    fund_moic: MetricPercentileBand
    probability_fund_moic_below_1: float
    deals: list[FundDealOutcome]
    factor_summaries: list[FundFactorSummary]
    correlation_effect: FundCorrelationEffect


# --- G73 Year-by-year value-creation waterfall ------------------------------------------------


class AnnualValueCreationYear(BaseModel):
    year_label: str
    period_label: str
    end_date: date
    months: int
    applied_multiple: float
    ebitda: float
    net_debt: float
    equity_value: float
    equity_change: float
    ebitda_growth: float
    multiple_change: float
    deleveraging: float
    cross_term: float
    reconciles: bool


class AnnualValueCreationResult(BaseModel):
    entry_multiple: float
    exit_multiple: float
    entry_ebitda: float
    entry_net_debt: float
    entry_equity: float
    exit_equity: float
    total_value_creation: float
    years: list[AnnualValueCreationYear]
    totals: dict[AttributionComponentKey, float]
    matches_attribution_total: bool
    reconciles: bool
