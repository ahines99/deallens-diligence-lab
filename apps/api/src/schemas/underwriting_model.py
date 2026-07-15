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


class DriverDistribution(BaseModel):
    """Sampling specification for one Monte Carlo driver.

    Drivers reuse the sensitivity variables: ``entry_multiple`` and ``exit_multiple`` are sampled
    as absolute EV/EBITDA turns, while ``base_rate_shift``, ``revenue_growth_shift``, and
    ``ebitda_margin_shift`` are sampled as additive shifts on the deterministic assumptions
    (``0.01`` = +100 bps). ``normal`` requires ``mean``/``std_dev``; ``uniform`` requires
    ``low``/``high``; ``triangular`` requires ``low``/``mode``/``high``.
    """

    driver: SensitivityVariable
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
