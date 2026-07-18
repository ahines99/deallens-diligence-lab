"""Deterministic private-equity underwriting calculations and version persistence."""

from __future__ import annotations

import ast
import calendar
import copy
import hashlib
import json
import math
import random
import statistics
from datetime import date, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import Deal
from src.models.underwriting_model import UnderwritingCaseDecision, UnderwritingCaseVersion
from src.schemas.deal_workflow import ActorContext
from src.schemas.underwriting_model import (
    AnnualValueCreationResult,
    CaseVarianceResult,
    CovenantHeadroomResult,
    DistributionParameters,
    DividendRecapSolveRequest,
    DividendRecapSolveResult,
    DriverModelRequest,
    DriverModelResult,
    ExitReadinessResult,
    FacilitySizingRequest,
    FacilitySizingResult,
    FootballFieldResult,
    FundDealSpec,
    FundMonteCarloRequest,
    FundMonteCarloResult,
    MonteCarloRequest,
    MonteCarloResult,
    OperatingPeriodAssumption,
    RecapBoltOnRequest,
    RecapBoltOnResult,
    ReturnsAttributionRequest,
    ReturnsAttributionResult,
    ReverseStressRequest,
    ReverseStressResult,
    SensitivityRequest,
    SensitivityResult,
    SensitivityTornadoRequest,
    SensitivityTornadoResult,
    UnderwritingAssumptions,
    UnderwritingCaseCreate,
    UnderwritingDecisionCreate,
    UnderwritingResult,
    ValuationTriangulationRequest,
    ValuationTriangulationResult,
    WorkingCapitalPegRequest,
    WorkingCapitalPegResult,
    WorkingCapitalSeasonalityRequest,
    WorkingCapitalSeasonalityResult,
)
from src.services.common import NotFound, get_workspace_or_404

SCHEMA_VERSION = "1.1"
_MONEY_DIGITS = 2
_RATIO_DIGITS = 8
_EPSILON = 1e-9


class UnderwritingCalculationError(ValueError):
    """Raised when internally consistent model inputs cannot produce a valid transaction."""


class CaseVersionConflict(ValueError):
    """Raised when optimistic version checks detect a stale editor."""


class CaseEvidenceError(ValueError):
    """Raised when a case attempts to bind non-governed private evidence."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def calculate_valuation_triangulation(
    request: ValuationTriangulationRequest,
) -> ValuationTriangulationResult:
    """Reconcile DCF, public-comparable, and precedent-transaction enterprise values.

    Comparable inputs are intentionally user/licensed-source supplied. Their source labels and
    optional evidence refs remain in the request rather than being fabricated from free market data.
    """
    candidates: list[dict] = []
    warnings: list[str] = []

    if request.dcf_enterprise_value is not None:
        value = request.dcf_enterprise_value
        candidates.append({
            "method": "dcf", "reference_count": 1,
            "multiple_low": None, "multiple_median": None, "multiple_high": None,
            "enterprise_value_low": value, "enterprise_value_median": value,
            "enterprise_value_high": value, "requested_weight": request.dcf_weight,
        })

    for method, references, requested_weight in (
        ("public_comps", request.public_comps, request.public_comps_weight),
        ("precedent_transactions", request.precedent_transactions, request.precedents_weight),
    ):
        if not references:
            continue
        multiples = sorted(reference.ev_ebitda_multiple for reference in references)
        low, median, high = multiples[0], statistics.median(multiples), multiples[-1]
        candidates.append({
            "method": method,
            "reference_count": len(references),
            "multiple_low": _ratio(low),
            "multiple_median": _ratio(median),
            "multiple_high": _ratio(high),
            "enterprise_value_low": _money(low * request.ebitda),
            "enterprise_value_median": _money(median * request.ebitda),
            "enterprise_value_high": _money(high * request.ebitda),
            "requested_weight": requested_weight,
        })
        uncited = sum(1 for reference in references if not reference.evidence_ref)
        if uncited:
            warnings.append(
                f"{uncited} {method.replace('_', ' ')} reference(s) have no evidence_ref"
            )

    if not candidates:
        raise UnderwritingCalculationError("No usable valuation methods were supplied")
    weight_total = sum(candidate["requested_weight"] for candidate in candidates)
    if weight_total <= 0:
        warnings.append("Available method weights were zero; equal weights were applied")
        for candidate in candidates:
            candidate["normalized_weight"] = 1.0 / len(candidates)
    else:
        for candidate in candidates:
            candidate["normalized_weight"] = candidate["requested_weight"] / weight_total

    blended_ev = sum(
        candidate["enterprise_value_median"] * candidate["normalized_weight"]
        for candidate in candidates
    )
    low = min(candidate["enterprise_value_low"] for candidate in candidates)
    high = max(candidate["enterprise_value_high"] for candidate in candidates)
    return ValuationTriangulationResult.model_validate({
        "ebitda": request.ebitda,
        "net_debt": request.net_debt,
        "methods": candidates,
        "blended_enterprise_value": _money(blended_ev),
        "blended_equity_value": _money(blended_ev - request.net_debt),
        "valuation_low": _money(low),
        "valuation_high": _money(high),
        "warnings": warnings,
    })


def _money(value: float) -> float:
    return round(float(value), _MONEY_DIGITS)


def _ratio(value: float) -> float:
    return round(float(value), _RATIO_DIGITS)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def standard_projection_periods(hold_period_years: float = 5.0) -> list[OperatingPeriodAssumption]:
    """Create monthly periods for years 1-2 and annual periods thereafter."""
    total_months = max(1, round(hold_period_years * 12))
    monthly_count = min(24, total_months)
    periods = [
        OperatingPeriodAssumption(label=f"M{index:02d}", months=1)
        for index in range(1, monthly_count + 1)
    ]
    remaining = total_months - monthly_count
    year_number = 3
    while remaining:
        months = min(12, remaining)
        label = f"Y{year_number}" if months == 12 else f"Y{year_number}P{months}"
        periods.append(OperatingPeriodAssumption(label=label, months=months))
        remaining -= months
        year_number += 1
    return periods


def _projection_periods(assumptions: UnderwritingAssumptions) -> list[OperatingPeriodAssumption]:
    return assumptions.projection.periods or standard_projection_periods(
        assumptions.transaction.hold_period_years
    )


def calculate_sources_uses(assumptions: UnderwritingAssumptions) -> dict:
    historical = assumptions.historical
    transaction = assumptions.transaction
    if historical.ltm_ebitda <= 0:
        raise UnderwritingCalculationError(
            "Positive LTM EBITDA is required for a multiple-based LBO"
        )

    entry_ev = transaction.entry_multiple * historical.ltm_ebitda
    equity_purchase_price = entry_ev - historical.existing_debt + historical.starting_cash
    if equity_purchase_price < 0:
        raise UnderwritingCalculationError("Calculated equity purchase price cannot be negative")

    financing_fees = sum(
        tranche.initial_amount * (tranche.oid_discount + tranche.financing_fee_percent)
        for tranche in assumptions.debt_tranches
    )
    uses = [
        {"name": "Purchase of equity", "amount": equity_purchase_price},
        {"name": "Refinance existing debt", "amount": historical.existing_debt},
        {"name": "Transaction fees", "amount": transaction.transaction_fees},
        {"name": "Financing fees and OID", "amount": financing_fees},
        {"name": "Management option cashout", "amount": transaction.management_options_cashout},
        {"name": "Other uses", "amount": transaction.other_uses},
        {"name": "Minimum cash", "amount": transaction.minimum_cash},
    ]
    sources = [{"name": "Acquired cash", "amount": historical.starting_cash}]
    sources.extend(
        {"name": f"New debt: {tranche.name}", "amount": tranche.initial_amount}
        for tranche in assumptions.debt_tranches
        if tranche.initial_amount
    )
    sources.append({"name": "Seller rollover", "amount": transaction.seller_rollover})

    total_uses = sum(line["amount"] for line in uses)
    committed_sources = sum(line["amount"] for line in sources)
    sponsor_equity = total_uses - committed_sources
    if sponsor_equity < -_EPSILON:
        raise UnderwritingCalculationError(
            "Transaction is overfunded: debt, acquired cash, and rollover exceed total uses"
        )
    sponsor_equity = max(0.0, sponsor_equity)
    sources.append({"name": "Sponsor equity", "amount": sponsor_equity})
    total_sources = sum(line["amount"] for line in sources)
    total_equity = sponsor_equity + transaction.seller_rollover
    if total_equity <= 0:
        raise UnderwritingCalculationError("Transaction requires a positive equity contribution")

    return {
        "entry_enterprise_value": _money(entry_ev),
        "equity_purchase_price": _money(equity_purchase_price),
        "uses": [dict(line, amount=_money(line["amount"])) for line in uses],
        "sources": [dict(line, amount=_money(line["amount"])) for line in sources],
        "total_uses": _money(total_uses),
        "total_sources": _money(total_sources),
        "sponsor_equity": _money(sponsor_equity),
        "rollover_equity": _money(transaction.seller_rollover),
        "sponsor_ownership": _ratio(sponsor_equity / total_equity),
        "balanced": abs(total_sources - total_uses) < 0.01,
    }


def xnpv(rate: float, cash_flows: list[tuple[date, float]]) -> float:
    if rate <= -1:
        raise ValueError("XNPV rate must be greater than -100%")
    if not cash_flows:
        raise ValueError("At least one cash flow is required")
    origin = min(flow_date for flow_date, _ in cash_flows)
    return sum(
        amount / (1.0 + rate) ** ((flow_date - origin).days / 365.0)
        for flow_date, amount in cash_flows
    )


def xirr(cash_flows: list[tuple[date, float]], guess: float = 0.20) -> float | None:
    """Solve dated IRR with Newton iterations and a broad bisection fallback."""
    if (
        not cash_flows
        or not any(amount < 0 for _, amount in cash_flows)
        or not any(amount > 0 for _, amount in cash_flows)
    ):
        return None
    origin = min(flow_date for flow_date, _ in cash_flows)
    rate = guess
    for _ in range(60):
        if rate <= -0.999999:
            break
        value = 0.0
        derivative = 0.0
        for flow_date, amount in cash_flows:
            years = (flow_date - origin).days / 365.0
            denominator = (1.0 + rate) ** years
            value += amount / denominator
            if years:
                derivative -= years * amount / ((1.0 + rate) ** (years + 1.0))
        if abs(value) < 1e-8:
            return _ratio(rate)
        if abs(derivative) < 1e-12:
            break
        candidate = rate - value / derivative
        if not math.isfinite(candidate) or candidate <= -0.999999:
            break
        if abs(candidate - rate) < 1e-10:
            return _ratio(candidate)
        rate = candidate

    low, high = -0.9999, 10.0
    low_value, high_value = xnpv(low, cash_flows), xnpv(high, cash_flows)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 10.0
        high_value = xnpv(high, cash_flows)
    if low_value * high_value > 0:
        return None
    for _ in range(200):
        midpoint = (low + high) / 2.0
        value = xnpv(midpoint, cash_flows)
        if abs(value) < 1e-8 or high - low < 1e-10:
            return _ratio(midpoint)
        if low_value * value <= 0:
            high = midpoint
        else:
            low, low_value = midpoint, value
    return _ratio((low + high) / 2.0)


def _resolved_drivers(assumptions: UnderwritingAssumptions, period) -> dict[str, float]:
    defaults = assumptions.projection.default_drivers.model_dump()
    overrides = period.model_dump(exclude_none=True)
    for field in defaults:
        if field in overrides:
            defaults[field] = overrides[field]
    return defaults


def _debt_schedule_rows(
    assumptions: UnderwritingAssumptions,
    period,
    drivers: dict[str, float],
    balances: dict[str, float],
    year_fraction: float,
) -> tuple[list[dict], float, float]:
    rows: list[dict] = []
    total_cash_interest = 0.0
    total_pik_interest = 0.0
    for tranche in assumptions.debt_tranches:
        opening = balances[tranche.name]
        cash_rate = max(drivers["base_rate"], tranche.base_rate_floor) + tranche.spread
        cash_interest = opening * cash_rate * year_fraction
        pik_interest = opening * tranche.pik_rate * year_fraction
        prepayment_balance = opening + pik_interest
        required_amortization = min(
            prepayment_balance,
            tranche.initial_amount * tranche.annual_amortization_rate * year_fraction,
        )
        if tranche.maturity_period == period.label:
            required_amortization = prepayment_balance
        rows.append(
            {
                "name": tranche.name,
                "tranche_type": tranche.tranche_type,
                "opening_balance": opening,
                "cash_rate": cash_rate,
                "cash_interest": cash_interest,
                "pik_interest": pik_interest,
                "prepayment_balance": prepayment_balance,
                "required_amortization": required_amortization,
                "paid_amortization": 0.0,
                "revolver_draw": 0.0,
                "cash_sweep": 0.0,
                "unpaid_amortization": 0.0,
                "ending_balance": prepayment_balance,
                "at_maturity": tranche.maturity_period == period.label,
            }
        )
        total_cash_interest += cash_interest
        total_pik_interest += pik_interest
    return rows, total_cash_interest, total_pik_interest


def _apply_debt_service(
    assumptions: UnderwritingAssumptions,
    debt_rows: list[dict],
    cash_before_debt: float,
) -> tuple[float, float, float, float, float]:
    minimum_cash = assumptions.transaction.minimum_cash
    required_total = sum(row["required_amortization"] for row in debt_rows)
    cash = cash_before_debt
    total_draw = 0.0

    # Draw committed revolvers only to fund required debt service and the minimum-cash reserve.
    need = max(0.0, minimum_cash + required_total - cash)
    tranche_by_name = {tranche.name: tranche for tranche in assumptions.debt_tranches}
    draw_priority = sorted(
        debt_rows,
        key=lambda row: tranche_by_name[row["name"]].cash_sweep_priority,
    )
    for row in draw_priority:
        tranche = tranche_by_name[row["name"]]
        if tranche.tranche_type != "revolver" or row["at_maturity"] or need <= _EPSILON:
            continue
        commitment = tranche.commitment or 0.0
        capacity = max(0.0, commitment - row["prepayment_balance"])
        draw = min(capacity, need)
        row["revolver_draw"] = draw
        row["prepayment_balance"] += draw
        row["ending_balance"] += draw
        cash += draw
        total_draw += draw
        need -= draw

    total_paid = 0.0
    payment_priority = sorted(
        debt_rows,
        key=lambda row: tranche_by_name[row["name"]].cash_sweep_priority,
    )
    for row in payment_priority:
        paid = min(row["required_amortization"], max(0.0, cash))
        row["paid_amortization"] = paid
        row["unpaid_amortization"] = row["required_amortization"] - paid
        row["ending_balance"] -= paid
        cash -= paid
        total_paid += paid

    sweep_budget = max(0.0, cash - minimum_cash) * assumptions.transaction.cash_sweep_percent
    total_sweep = 0.0
    priority = sorted(
        debt_rows,
        key=lambda row: tranche_by_name[row["name"]].cash_sweep_priority,
    )
    for row in priority:
        tranche = tranche_by_name[row["name"]]
        if not tranche.sweep_eligible or sweep_budget <= _EPSILON:
            continue
        swept = min(row["ending_balance"], sweep_budget)
        row["cash_sweep"] = swept
        row["ending_balance"] -= swept
        sweep_budget -= swept
        cash -= swept
        total_sweep += swept

    shortfall = max(0.0, minimum_cash - cash)
    # Ending cash may be negative: an unfunded deficit must carry into the next period
    # (and depress net debt, exit equity, and minimum liquidity) rather than be written off.
    return cash, total_draw, total_paid, total_sweep, shortfall


def _covenant_results(
    assumptions: UnderwritingAssumptions,
    period_label: str,
    annualized_ebitda: float,
    capex: float,
    cash_taxes: float,
    cash_interest: float,
    scheduled_amortization: float,
    ending_cash: float,
    debt_rows: list[dict],
) -> tuple[list[dict], dict[str, float | None]]:
    total_debt = sum(row["ending_balance"] for row in debt_rows)
    tranche_by_name = {tranche.name: tranche for tranche in assumptions.debt_tranches}
    senior_debt = sum(
        row["ending_balance"] for row in debt_rows if tranche_by_name[row["name"]].senior
    )
    undrawn_revolver = sum(
        max(
            0.0,
            (tranche.commitment or 0.0)
            - next(row["ending_balance"] for row in debt_rows if row["name"] == tranche.name),
        )
        for tranche in assumptions.debt_tranches
        if tranche.tranche_type == "revolver"
    )
    # Fixed charges are the CONTRACTUAL debt service — scheduled amortization, not the amount the
    # company managed to pay. Measuring paid amortization pins the ratio at ~1.0x in exactly the
    # cash-short regime a fixed-charge covenant exists to flag (partial payment reads as passing).
    fixed_charges = cash_interest + scheduled_amortization
    metrics: dict[str, float | None] = {
        "total_leverage": total_debt / annualized_ebitda if annualized_ebitda > 0 else None,
        "senior_leverage": senior_debt / annualized_ebitda if annualized_ebitda > 0 else None,
        "interest_coverage": annualized_ebitda / cash_interest if cash_interest > 0 else None,
        "fixed_charge_coverage": (
            (annualized_ebitda - capex - cash_taxes) / fixed_charges if fixed_charges > 0 else None
        ),
        "minimum_liquidity": ending_cash + undrawn_revolver,
    }
    # The caller annualizes EBITDA, interest, capex, tax, and amortization before this helper.
    results: list[dict] = []
    for covenant in assumptions.covenants:
        actual = metrics[covenant.metric]
        threshold = covenant.threshold_by_period.get(period_label, covenant.threshold)
        headroom = None
        passed = None
        if actual is not None:
            if covenant.test == "maximum":
                headroom = threshold - actual
                passed = actual <= threshold + _EPSILON
            else:
                headroom = actual - threshold
                passed = actual + _EPSILON >= threshold
        results.append(
            {
                "name": covenant.name,
                "metric": covenant.metric,
                "test": covenant.test,
                "actual": _ratio(actual) if actual is not None else None,
                "threshold": _ratio(threshold),
                "headroom": _ratio(headroom) if headroom is not None else None,
                "passed": passed,
            }
        )
    return results, metrics


def calculate_projection(
    assumptions: UnderwritingAssumptions,
    special_distributions: dict[str, float] | None = None,
) -> list[dict]:
    """Project operations, debt service, and covenants period by period.

    ``special_distributions`` (G70 seam) maps a period label to an equity distribution paid
    through that period's cash waterfall: it reduces cash before debt service, so the committed
    revolver may fund it and the period's covenants, liquidity, and every later period reflect
    it. Unknown labels and negative amounts fail closed rather than being silently dropped.
    """
    periods = _projection_periods(assumptions)
    distributions = dict(special_distributions or {})
    if distributions:
        unknown = sorted(set(distributions) - {period.label for period in periods})
        if unknown:
            raise UnderwritingCalculationError(
                "Special distribution periods are absent from the projection: "
                + ", ".join(unknown)
            )
        if any(amount < 0 for amount in distributions.values()):
            raise UnderwritingCalculationError("Special distributions cannot be negative")
    annualized_revenue = assumptions.historical.ltm_revenue
    beginning_nwc = assumptions.historical.starting_net_working_capital
    beginning_cash = assumptions.transaction.minimum_cash
    balances = {tranche.name: tranche.initial_amount for tranche in assumptions.debt_tranches}
    current_date = assumptions.transaction.close_date
    results: list[dict] = []

    for period in periods:
        year_fraction = period.months / 12.0
        drivers = _resolved_drivers(assumptions, period)
        period_start = current_date
        next_start = _add_months(period_start, period.months)
        period_end = next_start - timedelta(days=1)
        current_date = next_start

        beginning_revenue_run_rate = annualized_revenue
        annualized_revenue *= (1.0 + drivers["annual_revenue_growth"]) ** year_fraction
        growth_log = math.log1p(drivers["annual_revenue_growth"])
        if abs(growth_log) < _EPSILON:
            revenue = beginning_revenue_run_rate * year_fraction
        else:
            # Integrating the annualized revenue run-rate makes results invariant to whether
            # the same interval is expressed as monthly, quarterly, or annual periods.
            revenue = (
                beginning_revenue_run_rate
                * (math.exp(growth_log * year_fraction) - 1.0)
                / growth_log
            )
        gross_profit = revenue * drivers["gross_margin"]
        cost_of_goods_sold = revenue - gross_profit
        ebitda = revenue * drivers["ebitda_margin"]
        operating_expenses = gross_profit - ebitda
        da = revenue * drivers["da_percent_revenue"]
        ebit = ebitda - da
        ending_nwc = annualized_revenue * drivers["net_working_capital_percent_revenue"]
        change_nwc = ending_nwc - beginning_nwc
        capex = revenue * drivers["capex_percent_revenue"]

        debt_rows, cash_interest, pik_interest = _debt_schedule_rows(
            assumptions, period, drivers, balances, year_fraction
        )
        ebt = ebit - cash_interest - pik_interest
        cash_taxes = max(0.0, ebt) * drivers["cash_tax_rate"]
        net_income = ebt - cash_taxes
        unlevered_cash_taxes = max(0.0, ebit) * drivers["cash_tax_rate"]
        fcff = ebit - unlevered_cash_taxes + da - capex - change_nwc
        special_distribution = distributions.get(period.label, 0.0)
        cash_before_debt = (
            beginning_cash
            + ebitda
            - cash_taxes
            - capex
            - change_nwc
            - cash_interest
            - special_distribution
        )
        ending_cash, draw, paid_amort, sweep, liquidity_shortfall = _apply_debt_service(
            assumptions, debt_rows, cash_before_debt
        )
        balances = {row["name"]: max(0.0, row["ending_balance"]) for row in debt_rows}
        for row in debt_rows:
            row["ending_balance"] = balances[row["name"]]

        total_debt = sum(balances.values())
        annualized_ebitda = ebitda / year_fraction
        covenant_rows, covenant_metrics = _covenant_results(
            assumptions,
            period.label,
            annualized_ebitda,
            capex / year_fraction,
            cash_taxes / year_fraction,
            cash_interest / year_fraction,
            sum(row["required_amortization"] for row in debt_rows) / year_fraction,
            ending_cash,
            debt_rows,
        )

        public_debt_rows = []
        for row in debt_rows:
            public_debt_rows.append(
                {
                    key: (
                        _money(value) if key not in {"cash_rate", "tranche_type", "name"} else value
                    )
                    for key, value in row.items()
                    if key not in {"prepayment_balance", "at_maturity"}
                }
            )
            public_debt_rows[-1]["cash_rate"] = _ratio(row["cash_rate"])

        results.append(
            {
                "label": period.label,
                "start_date": period_start,
                "end_date": period_end,
                "months": period.months,
                "year_fraction": _ratio(year_fraction),
                "revenue": _money(revenue),
                "annualized_revenue": _money(annualized_revenue),
                "revenue_growth": _ratio(drivers["annual_revenue_growth"]),
                "cost_of_goods_sold": _money(cost_of_goods_sold),
                "gross_profit": _money(gross_profit),
                "operating_expenses": _money(operating_expenses),
                "ebitda": _money(ebitda),
                "ebitda_margin": _ratio(drivers["ebitda_margin"]),
                "depreciation_amortization": _money(da),
                "ebit": _money(ebit),
                "cash_interest": _money(cash_interest),
                "pik_interest": _money(pik_interest),
                "earnings_before_tax": _money(ebt),
                "cash_taxes": _money(cash_taxes),
                "net_income": _money(net_income),
                "net_working_capital": _money(ending_nwc),
                "change_in_net_working_capital": _money(change_nwc),
                "capex": _money(capex),
                "fcff": _money(fcff),
                "beginning_cash": _money(beginning_cash),
                "cash_before_debt_service": _money(cash_before_debt),
                "revolver_draw": _money(draw),
                "mandatory_amortization": _money(paid_amort),
                "cash_sweep": _money(sweep),
                "special_distribution": _money(special_distribution),
                "ending_cash": _money(ending_cash),
                "liquidity_shortfall": _money(liquidity_shortfall),
                "total_debt": _money(total_debt),
                "net_debt": _money(total_debt - ending_cash),
                "total_leverage": (
                    _ratio(covenant_metrics["total_leverage"])
                    if covenant_metrics["total_leverage"] is not None
                    else None
                ),
                "senior_leverage": (
                    _ratio(covenant_metrics["senior_leverage"])
                    if covenant_metrics["senior_leverage"] is not None
                    else None
                ),
                "interest_coverage": (
                    _ratio(covenant_metrics["interest_coverage"])
                    if covenant_metrics["interest_coverage"] is not None
                    else None
                ),
                "fixed_charge_coverage": (
                    _ratio(covenant_metrics["fixed_charge_coverage"])
                    if covenant_metrics["fixed_charge_coverage"] is not None
                    else None
                ),
                # Preserve None like the sibling covenant metrics: a not-computed minimum
                # liquidity must read as "n/a", never as $0 (which looks like zero liquidity).
                "liquidity": (
                    _money(covenant_metrics["minimum_liquidity"])
                    if covenant_metrics["minimum_liquidity"] is not None
                    else None
                ),
                "debt_tranches": public_debt_rows,
                "covenants": covenant_rows,
            }
        )
        beginning_nwc = ending_nwc
        beginning_cash = ending_cash
    return results


def calculate_dcf(assumptions: UnderwritingAssumptions, projection: list[dict]) -> dict:
    if not projection:
        raise UnderwritingCalculationError("At least one projection period is required")
    discount_rate = assumptions.valuation.discount_rate
    terminal_growth = assumptions.valuation.terminal_growth_rate
    elapsed = 0.0
    pv_explicit = 0.0
    for period in projection:
        year_fraction = period["year_fraction"]
        elapsed += year_fraction
        exponent = (
            elapsed - year_fraction / 2.0 if assumptions.valuation.mid_year_convention else elapsed
        )
        pv_explicit += period["fcff"] / (1.0 + discount_rate) ** exponent

    final_period = projection[-1]
    terminal_fcff = final_period["fcff"] / final_period["year_fraction"]
    terminal_value = terminal_fcff * (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    # Under the mid-year convention the perpetuity's annual flows also arrive mid-year,
    # so the terminal value discounts over half a year less than the explicit horizon.
    terminal_exponent = (
        elapsed - 0.5 if assumptions.valuation.mid_year_convention else elapsed
    )
    pv_terminal = terminal_value / (1.0 + discount_rate) ** terminal_exponent
    enterprise_value = pv_explicit + pv_terminal
    net_debt = assumptions.historical.existing_debt - assumptions.historical.starting_cash
    equity_value = enterprise_value - net_debt
    terminal_share = pv_terminal / enterprise_value if enterprise_value else None
    return {
        "discount_rate": _ratio(discount_rate),
        "terminal_growth_rate": _ratio(terminal_growth),
        "pv_explicit_fcff": _money(pv_explicit),
        "terminal_value": _money(terminal_value),
        "pv_terminal_value": _money(pv_terminal),
        "enterprise_value": _money(enterprise_value),
        "net_debt": _money(net_debt),
        "equity_value": _money(equity_value),
        "terminal_value_percent": _ratio(terminal_share) if terminal_share is not None else None,
    }


def _available_revolver(assumptions: UnderwritingAssumptions, period: dict) -> float:
    rows = {row["name"]: row for row in period["debt_tranches"]}
    return sum(
        max(0.0, (tranche.commitment or 0.0) - rows[tranche.name]["ending_balance"])
        for tranche in assumptions.debt_tranches
        if tranche.tranche_type == "revolver"
    )


def calculate_returns(
    assumptions: UnderwritingAssumptions, sources_uses: dict, projection: list[dict]
) -> dict:
    final_period = projection[-1]
    exit_ebitda = final_period["ebitda"] / final_period["year_fraction"]
    exit_ev = assumptions.transaction.exit_multiple * exit_ebitda
    exit_debt = final_period["total_debt"]
    exit_cash = final_period["ending_cash"]
    exit_equity = exit_ev - exit_debt + exit_cash
    sponsor_exit = exit_equity * sources_uses["sponsor_ownership"]
    invested = sources_uses["sponsor_equity"]
    cash_flows = [
        (assumptions.transaction.close_date, -invested),
        (final_period["end_date"], sponsor_exit),
    ]
    moic = sponsor_exit / invested if invested > 0 else None
    calculated_xirr = xirr(cash_flows)
    return {
        "exit_enterprise_value": _money(exit_ev),
        "exit_debt": _money(exit_debt),
        "exit_cash": _money(exit_cash),
        "exit_equity_value": _money(exit_equity),
        "sponsor_exit_proceeds": _money(sponsor_exit),
        "sponsor_invested_capital": _money(invested),
        "moic": _ratio(moic) if moic is not None else None,
        "xirr": calculated_xirr,
        "cash_flows": [
            {"date": flow_date.isoformat(), "amount": _money(amount)}
            for flow_date, amount in cash_flows
        ],
    }


def calculate_summary(assumptions: UnderwritingAssumptions, projection: list[dict]) -> dict:
    total_years = sum(period["year_fraction"] for period in projection)
    final = projection[-1]
    final_revenue = final["annualized_revenue"]
    revenue_cagr = (
        (final_revenue / assumptions.historical.ltm_revenue) ** (1.0 / total_years) - 1.0
        if total_years > 0 and assumptions.historical.ltm_revenue > 0
        else None
    )
    exit_ebitda = final["ebitda"] / final["year_fraction"]
    minimum_liquidity = min(
        period["ending_cash"] + _available_revolver(assumptions, period) for period in projection
    )
    leverage_values = []
    first_breach = None
    first_default = None
    for period in projection:
        annual_ebitda = period["ebitda"] / period["year_fraction"]
        if annual_ebitda > 0:
            leverage_values.append(period["total_debt"] / annual_ebitda)
        if first_breach is None and any(row["passed"] is False for row in period["covenants"]):
            first_breach = period["label"]
        if first_default is None and any(
            row["unpaid_amortization"] > 0.005 for row in period["debt_tranches"]
        ):
            first_default = period["label"]
    return {
        "revenue_cagr": _ratio(revenue_cagr) if revenue_cagr is not None else None,
        "exit_ebitda": _money(exit_ebitda),
        "exit_ebitda_margin": _ratio(final["ebitda_margin"]),
        "minimum_liquidity": _money(minimum_liquidity),
        "maximum_total_leverage": _ratio(max(leverage_values)) if leverage_values else None,
        "first_covenant_breach": first_breach,
        "first_debt_service_default": first_default,
    }


def run_underwriting(assumptions: UnderwritingAssumptions) -> UnderwritingResult:
    """Run sources & uses, operating/debt projection, DCF, and sponsor returns."""
    sources_uses = calculate_sources_uses(assumptions)
    projection = calculate_projection(assumptions)
    if not projection:
        raise UnderwritingCalculationError("At least one projection period is required")
    dcf = calculate_dcf(assumptions, projection)
    returns = calculate_returns(assumptions, sources_uses, projection)
    summary = calculate_summary(assumptions, projection)
    return UnderwritingResult.model_validate(
        {
            "currency": assumptions.currency,
            "sources_uses": sources_uses,
            "projection": projection,
            "dcf": dcf,
            "returns": returns,
            "summary": summary,
            "generated_at": now_utc(),
        }
    )


def calculate_working_capital_peg(payload: WorkingCapitalPegRequest) -> WorkingCapitalPegResult:
    rows = sorted(payload.observations, key=lambda row: row.observation_date)
    eligible = [row for row in rows if row.observation_date <= payload.closing_date]
    if not eligible:
        raise UnderwritingCalculationError("No working-capital observation predates closing")
    cutoff = _add_months(payload.closing_date, -12)
    # Strictly after the cutoff: a trailing-twelve-month window over monthly observations holds 12
    # points. ``>=`` also admits the observation exactly 12 months before closing, double-weighting
    # the anniversary month in the peg (and therefore the purchase-price adjustment).
    trailing = [row for row in eligible if row.observation_date > cutoff] or eligible

    def normalized(row) -> float:
        return (
            row.accounts_receivable
            + row.inventory
            + row.other_operating_current_assets
            - row.accounts_payable
            - row.accrued_liabilities
            - row.deferred_revenue
            - row.other_operating_current_liabilities
            - row.excluded_net_amount
        )

    normalized_rows = [
        {
            "observation_date": row.observation_date,
            "normalized_working_capital": _money(normalized(row)),
        }
        for row in eligible
    ]
    trailing_values = [normalized(row) for row in trailing]
    seasonal_rows = [
        row for row in eligible if row.observation_date.month == payload.closing_date.month
    ]
    seasonal_average = (
        statistics.fmean(normalized(row) for row in seasonal_rows) if seasonal_rows else None
    )
    trailing_average = statistics.fmean(trailing_values)
    trailing_median = statistics.median(trailing_values)
    if payload.method == "median_ltm":
        peg = trailing_median
    elif payload.method == "average_ltm":
        peg = trailing_average
    else:
        peg = seasonal_average if seasonal_average is not None else trailing_average
    adjustment = (
        payload.delivered_working_capital - peg
        if payload.delivered_working_capital is not None
        else None
    )
    return WorkingCapitalPegResult.model_validate(
        {
            "method": payload.method,
            "peg": _money(peg),
            "trailing_average": _money(trailing_average),
            "trailing_median": _money(trailing_median),
            "low": _money(min(trailing_values)),
            "high": _money(max(trailing_values)),
            "seasonal_month": payload.closing_date.month,
            "seasonal_average": _money(seasonal_average) if seasonal_average is not None else None,
            "delivered_working_capital": payload.delivered_working_capital,
            "purchase_price_adjustment": _money(adjustment) if adjustment is not None else None,
            "observations": normalized_rows,
        }
    )


def _apply_variable(
    assumptions: UnderwritingAssumptions, variable: str, value: float
) -> UnderwritingAssumptions:
    data = assumptions.model_dump(mode="json")
    if variable in {"entry_multiple", "exit_multiple"}:
        data["transaction"][variable] = value
    else:
        if variable == "base_rate_shift":
            field = "base_rate"
        elif variable == "revenue_growth_shift":
            field = "annual_revenue_growth"
        elif variable == "ebitda_margin_shift":
            field = "ebitda_margin"
        else:  # pragma: no cover - guarded by schema literals
            raise UnderwritingCalculationError(f"Unsupported sensitivity variable: {variable}")
        data["projection"]["default_drivers"][field] += value
        for period in data["projection"]["periods"]:
            if period.get(field) is not None:
                period[field] += value
    try:
        return UnderwritingAssumptions.model_validate(data)
    except ValidationError as exc:
        raise UnderwritingCalculationError(
            f"Sensitivity value {value} makes {variable} invalid: {exc.errors()[0]['msg']}"
        ) from exc


def _result_metric(result: UnderwritingResult, metric: str) -> float | None:
    if metric == "irr":
        return result.returns.xirr
    if metric == "moic":
        return result.returns.moic
    if metric == "minimum_liquidity":
        return result.summary.minimum_liquidity
    raise UnderwritingCalculationError(f"Unsupported output metric: {metric}")


def calculate_sensitivity(payload: SensitivityRequest) -> SensitivityResult:
    grid: list[list[float | None]] = []
    for row_value in payload.rows.values:
        row: list[float | None] = []
        for column_value in payload.columns.values:
            scenario = _apply_variable(payload.assumptions, payload.rows.variable, row_value)
            scenario = _apply_variable(scenario, payload.columns.variable, column_value)
            row.append(_result_metric(run_underwriting(scenario), payload.metric))
        grid.append(row)
    return SensitivityResult(
        row_variable=payload.rows.variable,
        row_values=payload.rows.values,
        column_variable=payload.columns.variable,
        column_values=payload.columns.values,
        metric=payload.metric,
        grid=grid,
    )


def calculate_reverse_stress(payload: ReverseStressRequest) -> ReverseStressResult:
    def evaluate(value: float) -> float | None:
        scenario = _apply_variable(payload.assumptions, payload.variable, value)
        return _result_metric(run_underwriting(scenario), payload.objective)

    low, high = payload.lower_bound, payload.upper_bound
    low_value, high_value = evaluate(low), evaluate(high)
    if low_value is None or high_value is None:
        return ReverseStressResult(
            status="no_solution",
            variable=payload.variable,
            objective=payload.objective,
            target=payload.target,
            solved_value=None,
            achieved_value=None,
            lower_value=low_value,
            upper_value=high_value,
            iterations=0,
        )
    low_delta, high_delta = low_value - payload.target, high_value - payload.target
    if abs(low_delta) <= payload.tolerance:
        return ReverseStressResult(
            status="solved",
            variable=payload.variable,
            objective=payload.objective,
            target=payload.target,
            solved_value=low,
            achieved_value=low_value,
            lower_value=low_value,
            upper_value=high_value,
            iterations=0,
        )
    if abs(high_delta) <= payload.tolerance:
        return ReverseStressResult(
            status="solved",
            variable=payload.variable,
            objective=payload.objective,
            target=payload.target,
            solved_value=high,
            achieved_value=high_value,
            lower_value=low_value,
            upper_value=high_value,
            iterations=0,
        )
    if low_delta * high_delta > 0:
        return ReverseStressResult(
            status="no_solution",
            variable=payload.variable,
            objective=payload.objective,
            target=payload.target,
            solved_value=None,
            achieved_value=None,
            lower_value=low_value,
            upper_value=high_value,
            iterations=0,
        )

    achieved = None
    midpoint = (low + high) / 2.0
    for iteration in range(1, payload.max_iterations + 1):
        midpoint = (low + high) / 2.0
        achieved = evaluate(midpoint)
        if achieved is None:
            break
        delta = achieved - payload.target
        if abs(delta) <= payload.tolerance or high - low <= payload.tolerance:
            return ReverseStressResult(
                status="solved",
                variable=payload.variable,
                objective=payload.objective,
                target=payload.target,
                solved_value=_ratio(midpoint),
                achieved_value=achieved,
                lower_value=low_value,
                upper_value=high_value,
                iterations=iteration,
            )
        if low_delta * delta <= 0:
            high = midpoint
        else:
            low, low_delta = midpoint, delta
    return ReverseStressResult(
        status="no_solution",
        variable=payload.variable,
        objective=payload.objective,
        target=payload.target,
        solved_value=None,
        achieved_value=achieved,
        lower_value=low_value,
        upper_value=high_value,
        iterations=payload.max_iterations,
    )


_MONTE_CARLO_PERCENTILES = (5.0, 25.0, 50.0, 75.0, 95.0)


def _sample_driver(rng: random.Random, distribution: DistributionParameters) -> float:
    if distribution.kind == "normal":
        return rng.gauss(distribution.mean, distribution.std_dev)
    if distribution.kind == "uniform":
        return rng.uniform(distribution.low, distribution.high)
    return rng.triangular(distribution.low, distribution.high, distribution.mode)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    """Linear-interpolation percentile over an ascending list (matches numpy's default)."""
    if not sorted_values:
        raise UnderwritingCalculationError("Cannot compute percentiles of an empty sample")
    rank = (len(sorted_values) - 1) * percentile / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    fraction = rank - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _metric_band(values: list[float]) -> dict:
    ordered = sorted(values)
    band = {
        f"p{int(percentile)}": _ratio(_percentile(ordered, percentile))
        for percentile in _MONTE_CARLO_PERCENTILES
    }
    band["mean"] = _ratio(statistics.fmean(ordered))
    return band


def run_monte_carlo(payload: MonteCarloRequest) -> MonteCarloResult:
    """Sample driver distributions and re-run the deterministic LBO engine per iteration.

    A single ``random.Random(seed)`` generator drives every draw, so identical seed + inputs
    always reproduce identical output. Iterations whose sampled drivers make the assumptions
    invalid, or whose IRR/MoIC do not converge, are skipped and counted as ``failed``. An equity
    wipeout is NOT a failure: when sponsor exit proceeds are <= 0 no IRR solves (there is no
    positive cash flow), but the economic outcome is a total loss — it enters the sample with its
    computed MoIC and an IRR of -100%. Censoring wipeouts into ``failed`` would understate every
    loss statistic exactly when the tail risk is worst.
    """
    rng = random.Random(payload.seed)
    irr_values: list[float] = []
    moic_values: list[float] = []
    samples: dict[str, list[float]] = {
        distribution.driver: [] for distribution in payload.distributions
    }
    failed = 0
    for _ in range(payload.iterations):
        draws = [
            (distribution, _sample_driver(rng, distribution))
            for distribution in payload.distributions
        ]
        for distribution, value in draws:
            samples[distribution.driver].append(value)
        try:
            scenario = payload.assumptions
            for distribution, value in draws:
                scenario = _apply_variable(scenario, distribution.driver, value)
            sources_uses = calculate_sources_uses(scenario)
            projection = calculate_projection(scenario)
            returns = calculate_returns(scenario, sources_uses, projection)
        except UnderwritingCalculationError:
            failed += 1
            continue
        irr, moic = returns["xirr"], returns["moic"]
        if irr is None and moic is not None and returns["sponsor_exit_proceeds"] <= 0:
            irr = -1.0
        if irr is None or moic is None:
            failed += 1
            continue
        irr_values.append(irr)
        moic_values.append(moic)

    converged = len(irr_values)
    if converged == 0:
        raise UnderwritingCalculationError(
            "No Monte Carlo iteration produced a converged IRR and MoIC"
        )
    return MonteCarloResult.model_validate(
        {
            "iterations": payload.iterations,
            "seed": payload.seed,
            "converged": converged,
            "failed": failed,
            "irr": _metric_band(irr_values),
            "moic": _metric_band(moic_values),
            "probability_irr_below_zero": _ratio(
                sum(1 for value in irr_values if value < 0) / converged
            ),
            "probability_moic_below_1": _ratio(
                sum(1 for value in moic_values if value < 1.0) / converged
            ),
            "driver_summaries": [
                {
                    "driver": distribution.driver,
                    "kind": distribution.kind,
                    "sampled_mean": _ratio(statistics.fmean(samples[distribution.driver])),
                    "sampled_min": _ratio(min(samples[distribution.driver])),
                    "sampled_max": _ratio(max(samples[distribution.driver])),
                }
                for distribution in payload.distributions
            ],
        }
    )


def calculate_returns_attribution(payload: ReturnsAttributionRequest) -> ReturnsAttributionResult:
    """Decompose total equity value creation with the standard PE bridge, exactly reconciled.

    Entry state comes from the transaction assumptions (entry net debt is total new debt less the
    funded minimum-cash balance, mirroring the projection's opening cash). Exit state comes from
    the deterministic engine's final period. Legs use Decimal arithmetic and the cross term is
    computed as ``total - other legs`` so the components always sum exactly to the total.
    """
    assumptions = payload.assumptions
    sources_uses = calculate_sources_uses(assumptions)
    projection = calculate_projection(assumptions)
    returns = calculate_returns(assumptions, sources_uses, projection)

    cent = Decimal("0.01")
    entry_multiple = Decimal(str(assumptions.transaction.entry_multiple))
    exit_multiple = Decimal(str(assumptions.transaction.exit_multiple))
    entry_ebitda = Decimal(str(assumptions.historical.ltm_ebitda))
    final = projection[-1]
    exit_ebitda = Decimal(str(_money(final["ebitda"] / final["year_fraction"])))
    entry_net_debt = sum(
        (Decimal(str(tranche.initial_amount)) for tranche in assumptions.debt_tranches),
        Decimal("0"),
    ) - Decimal(str(assumptions.transaction.minimum_cash))
    exit_net_debt = Decimal(str(returns["exit_debt"])) - Decimal(str(returns["exit_cash"]))

    entry_equity = (entry_multiple * entry_ebitda - entry_net_debt).quantize(cent)
    exit_equity = (exit_multiple * exit_ebitda - exit_net_debt).quantize(cent)
    total = exit_equity - entry_equity

    ebitda_growth = (entry_multiple * (exit_ebitda - entry_ebitda)).quantize(cent)
    multiple_change = ((exit_multiple - entry_multiple) * entry_ebitda).quantize(cent)
    deleveraging = (entry_net_debt - exit_net_debt).quantize(cent)
    cross_term = total - ebitda_growth - multiple_change - deleveraging

    components = [
        ("ebitda_growth", "EBITDA growth at entry multiple", ebitda_growth),
        ("multiple_change", "Multiple expansion (contraction)", multiple_change),
        ("deleveraging", "Net-debt paydown (deleveraging)", deleveraging),
        ("cross_term", "Cross term (multiple x EBITDA interaction)", cross_term),
    ]
    reconciles = sum(amount for _, _, amount in components) == total
    return ReturnsAttributionResult.model_validate(
        {
            "entry_multiple": _ratio(assumptions.transaction.entry_multiple),
            "entry_ebitda": float(entry_ebitda),
            "entry_net_debt": float(entry_net_debt.quantize(cent)),
            "entry_equity": float(entry_equity),
            "exit_multiple": _ratio(assumptions.transaction.exit_multiple),
            "exit_ebitda": float(exit_ebitda),
            "exit_net_debt": float(exit_net_debt.quantize(cent)),
            "exit_equity": float(exit_equity),
            "total_value_creation": float(total),
            "components": [
                {
                    "key": key,
                    "label": label,
                    "amount": float(amount),
                    "share_of_total": _ratio(float(amount / total)) if total != 0 else None,
                }
                for key, label, amount in components
            ],
            "reconciles": reconciles,
        }
    )


def calculate_covenant_headroom(assumptions: UnderwritingAssumptions) -> CovenantHeadroomResult:
    """Project quarter-by-quarter covenant headroom and flag the first breach per covenant.

    Reuses the deterministic projection's covenant machinery: ``headroom`` is the sign-aware
    slack from the engine (positive = compliant for both maximum and minimum tests) and a period
    is ``breached`` exactly when its covenant test fails. The threshold-crossing quarter is the
    first period whose headroom goes negative.
    """
    if not assumptions.covenants:
        raise UnderwritingCalculationError(
            "At least one covenant is required for a headroom projection"
        )
    projection = calculate_projection(assumptions)
    covenants_out: list[dict] = []
    # ``_covenant_results`` emits one row per covenant in ``assumptions.covenants`` order, so the
    # same positional index identifies a covenant across every projection period.
    for index, covenant in enumerate(assumptions.covenants):
        periods_out: list[dict] = []
        first_breach_period: str | None = None
        breached_any = False
        for period in projection:
            row = period["covenants"][index]
            breached = row["passed"] is False
            if breached:
                breached_any = True
                if first_breach_period is None:
                    first_breach_period = period["label"]
            periods_out.append(
                {
                    "period_label": period["label"],
                    "start_date": period["start_date"],
                    "end_date": period["end_date"],
                    "actual": row["actual"],
                    "threshold": row["threshold"],
                    "headroom": row["headroom"],
                    "breached": breached,
                }
            )
        covenants_out.append(
            {
                "name": covenant.name,
                "metric": covenant.metric,
                "test": covenant.test,
                "periods": periods_out,
                "first_breach_period": first_breach_period,
                "breached": breached_any,
            }
        )
    return CovenantHeadroomResult.model_validate(
        {
            "currency": assumptions.currency,
            "covenants": covenants_out,
            "generated_at": now_utc(),
        }
    )


_VARIANCE_LINES: tuple[tuple[str, str], ...] = (
    ("entry_multiple", "Entry EV/EBITDA multiple"),
    ("exit_multiple", "Exit EV/EBITDA multiple"),
    ("exit_revenue", "Exit annualized revenue"),
    ("exit_ebitda", "Exit EBITDA"),
    ("entry_enterprise_value", "Entry enterprise value"),
    ("sponsor_equity", "Sponsor equity invested"),
    ("exit_equity_value", "Exit equity value"),
    ("maximum_total_leverage", "Peak total leverage"),
    ("irr", "Sponsor IRR"),
    ("moic", "Sponsor MoIC"),
)


def _variance_metrics(assumptions: UnderwritingAssumptions) -> dict[str, float | None]:
    result = run_underwriting(assumptions)
    final = result.projection[-1]
    return {
        "entry_multiple": assumptions.transaction.entry_multiple,
        "exit_multiple": assumptions.transaction.exit_multiple,
        "exit_revenue": final.annualized_revenue,
        "exit_ebitda": result.summary.exit_ebitda,
        "entry_enterprise_value": result.sources_uses.entry_enterprise_value,
        "sponsor_equity": result.sources_uses.sponsor_equity,
        "exit_equity_value": result.returns.exit_equity_value,
        "maximum_total_leverage": result.summary.maximum_total_leverage,
        "irr": result.returns.xirr,
        "moic": result.returns.moic,
    }


def calculate_case_variance(
    management: UnderwritingAssumptions,
    sponsor: UnderwritingAssumptions,
    management_label: str = "management",
    sponsor_label: str = "sponsor",
) -> CaseVarianceResult:
    """Line-level deltas (management minus sponsor) ranked by absolute percentage materiality.

    ``absolute_delta`` uses Decimal so it reconciles exactly to the two reported values. Lines
    whose percentage delta cannot be computed (a missing value or a zero sponsor base) rank last.
    """
    mgmt_metrics = _variance_metrics(management)
    spon_metrics = _variance_metrics(sponsor)
    lines: list[dict] = []
    for key, label in _VARIANCE_LINES:
        mgmt_value = mgmt_metrics[key]
        spon_value = spon_metrics[key]
        absolute_delta: float | None = None
        pct_delta: float | None = None
        if mgmt_value is not None and spon_value is not None:
            absolute_delta = float(Decimal(str(mgmt_value)) - Decimal(str(spon_value)))
            if spon_value != 0:
                pct_delta = _ratio(absolute_delta / spon_value)
        lines.append(
            {
                "key": key,
                "label": label,
                "management_value": mgmt_value,
                "sponsor_value": spon_value,
                "absolute_delta": absolute_delta,
                "pct_delta": pct_delta,
            }
        )

    def sort_key(line: dict) -> tuple[int, float]:
        pct = line["pct_delta"]
        if pct is None:
            return (1, 0.0)
        return (0, -abs(pct))

    ranked = sorted(lines, key=sort_key)
    for rank, line in enumerate(ranked, start=1):
        line["materiality_rank"] = rank
    return CaseVarianceResult.model_validate(
        {
            "management_label": management_label,
            "sponsor_label": sponsor_label,
            "lines": ranked,
            "generated_at": now_utc(),
        }
    )


_EXIT_HOLD_PERIODS: tuple[float, ...] = (3.0, 5.0, 7.0)


def _score_dimension(
    dimension: str, metric: str, value: float | None, threshold: float, direction: str
) -> dict:
    if value is None:
        return {
            "dimension": dimension,
            "metric": metric,
            "value": None,
            "threshold": _ratio(threshold),
            "direction": direction,
            "meets_threshold": None,
            "score": 0.0,
            "rating": "insufficient_data",
        }
    if direction == "higher_is_better":
        meets = value + _EPSILON >= threshold
        raw = 50.0 * value / threshold if threshold > 0 else (100.0 if value >= 0 else 0.0)
    else:
        meets = value <= threshold + _EPSILON
        raw = 50.0 * threshold / value if value > 0 else 100.0
    score = round(max(0.0, min(100.0, raw)), 2)
    rating = "strong" if score >= 75.0 else "adequate" if score >= 50.0 else "weak"
    return {
        "dimension": dimension,
        "metric": metric,
        "value": _ratio(value),
        "threshold": _ratio(threshold),
        "direction": direction,
        "meets_threshold": meets,
        "score": score,
        "rating": rating,
    }


def _rescope_hold(assumptions: UnderwritingAssumptions, hold_years: float) -> UnderwritingAssumptions:
    if math.isclose(hold_years, assumptions.transaction.hold_period_years, abs_tol=1e-9):
        # The unchanged hold must reproduce the headline case exactly — including per-period
        # driver overrides. Regenerating periods here made the grid row disagree with the
        # deterministic result for the very same hold.
        return assumptions
    data = assumptions.model_dump(mode="json")
    data["transaction"]["hold_period_years"] = hold_years
    # Regenerate standard periods so the projection spans the requested hold under the same
    # default drivers; explicit per-period overrides do not carry to a different horizon.
    data["projection"]["periods"] = []
    try:
        return UnderwritingAssumptions.model_validate(data)
    except ValidationError as exc:
        raise UnderwritingCalculationError(
            f"Cannot rescope to a {hold_years:g}-year hold: {exc.errors()[0]['msg']}"
        ) from exc


def calculate_exit_readiness(assumptions: UnderwritingAssumptions) -> ExitReadinessResult:
    """Score exit readiness across leverage/growth/margin/coverage and grid IRR/MoIC by hold.

    Each dimension names an explicit threshold. The hold-period grid re-runs the deterministic
    engine at 3/5/7-year horizons (reusing ``calculate_projection`` and ``xirr`` via
    ``run_underwriting``) so a longer hold's returns can be compared on the same drivers.
    """
    result = run_underwriting(assumptions)
    projection = result.projection
    coverage_values = [
        period.interest_coverage
        for period in projection
        if period.interest_coverage is not None
    ]
    min_coverage = min(coverage_values) if coverage_values else None
    if min_coverage is None and all(period.cash_interest == 0 for period in projection):
        # Debt-free (no cash interest in any period): coverage is trivially satisfied, not
        # "insufficient data" — there are no interest charges to cover.
        coverage_dimension = {
            "dimension": "coverage",
            "metric": "minimum_interest_coverage",
            "value": None,
            "threshold": _ratio(2.0),
            "direction": "higher_is_better",
            "meets_threshold": True,
            "score": 100.0,
            "rating": "strong",
        }
    else:
        coverage_dimension = _score_dimension(
            "coverage", "minimum_interest_coverage", min_coverage, 2.0, "higher_is_better",
        )
    dimensions = [
        _score_dimension(
            "leverage", "maximum_total_leverage",
            result.summary.maximum_total_leverage, 4.0, "lower_is_better",
        ),
        _score_dimension(
            "growth", "revenue_cagr", result.summary.revenue_cagr, 0.05, "higher_is_better",
        ),
        _score_dimension(
            "margin", "exit_ebitda_margin",
            result.summary.exit_ebitda_margin, 0.20, "higher_is_better",
        ),
        coverage_dimension,
    ]
    overall_score = round(
        statistics.fmean(dimension["score"] for dimension in dimensions), 2
    )
    overall_rating = (
        "strong" if overall_score >= 75.0 else "adequate" if overall_score >= 50.0 else "weak"
    )

    grid: list[dict] = []
    for hold_years in _EXIT_HOLD_PERIODS:
        scenario = _rescope_hold(assumptions, hold_years)
        scenario_result = run_underwriting(scenario)
        grid.append(
            {
                "hold_period_years": hold_years,
                "irr": scenario_result.returns.xirr,
                "moic": scenario_result.returns.moic,
                "exit_ebitda": scenario_result.summary.exit_ebitda,
                "exit_equity_value": scenario_result.returns.exit_equity_value,
            }
        )
    return ExitReadinessResult.model_validate(
        {
            "dimensions": dimensions,
            "overall_score": overall_score,
            "overall_rating": overall_rating,
            "hold_period_grid": grid,
            "generated_at": now_utc(),
        }
    )


_FOOTBALL_FIELD_METHODS: tuple[str, ...] = ("dcf", "public_comps", "precedent_transactions")
_FOOTBALL_FIELD_LABELS = {
    "dcf": "Discounted cash flow",
    "public_comps": "Public comparables",
    "precedent_transactions": "Precedent transactions",
}
_FOOTBALL_FIELD_EXCLUSIONS = {
    "dcf": "No DCF enterprise value provided",
    "public_comps": "No comparable peers provided",
    "precedent_transactions": "No precedent transactions provided",
}


def calculate_football_field(request: ValuationTriangulationRequest) -> FootballFieldResult:
    """Normalize the triangulation into chart-ready bars with explicit weights and exclusions.

    Every canonical method appears once, in fixed order. A method with no inputs is excluded with
    an explicit reason and carries null bounds and zero weight — never imputed to a fabricated
    value. Included methods keep the triangulation's normalized weights, which sum to 1.
    """
    triangulation = calculate_valuation_triangulation(request)
    by_method = {method.method: method for method in triangulation.methods}
    methods_out: list[dict] = []
    for method in _FOOTBALL_FIELD_METHODS:
        entry = by_method.get(method)
        if entry is not None:
            methods_out.append(
                {
                    "method": method,
                    "label": _FOOTBALL_FIELD_LABELS[method],
                    "reference_count": entry.reference_count,
                    "low": entry.enterprise_value_low,
                    "mid": entry.enterprise_value_median,
                    "high": entry.enterprise_value_high,
                    "weight": _ratio(entry.normalized_weight),
                    "included": True,
                    "excluded_reason": None,
                }
            )
        else:
            methods_out.append(
                {
                    "method": method,
                    "label": _FOOTBALL_FIELD_LABELS[method],
                    "reference_count": 0,
                    "low": None,
                    "mid": None,
                    "high": None,
                    "weight": 0.0,
                    "included": False,
                    "excluded_reason": _FOOTBALL_FIELD_EXCLUSIONS[method],
                }
            )
    included_weight_total = _ratio(
        sum(method["weight"] for method in methods_out if method["included"])
    )
    return FootballFieldResult.model_validate(
        {
            "ebitda": triangulation.ebitda,
            "net_debt": triangulation.net_debt,
            "methods": methods_out,
            "included_weight_total": included_weight_total,
            "blended_enterprise_value": triangulation.blended_enterprise_value,
            "blended_equity_value": triangulation.blended_equity_value,
            "valuation_low": triangulation.valuation_low,
            "valuation_high": triangulation.valuation_high,
            "warnings": triangulation.warnings,
            "generated_at": now_utc(),
        }
    )


# --- G24 Driver-based operating model ---------------------------------------------------------
#
# Formulas are evaluated by a *whitelisted AST walk*, never ``eval``/``exec``. Only a fixed set of
# node types is permitted; anything else (function calls, attribute access, subscripts, comparisons,
# names that are not declared drivers) is rejected before any arithmetic runs.

_ALLOWED_DRIVER_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
    ast.UAdd,
    ast.Name,
    ast.Load,
    ast.Constant,
)


def _parse_driver_formula(name: str, formula: str) -> tuple[ast.Expression, set[str]]:
    """Parse one formula into an AST, rejecting any non-whitelisted node; return its name refs."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise UnderwritingCalculationError(
            f"Driver '{name}' has an invalid formula: {exc.msg}"
        ) from exc
    references: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_DRIVER_NODES):
            raise UnderwritingCalculationError(
                f"Driver '{name}' uses an unsupported expression ({type(node).__name__}); only "
                "+ - * /, parentheses, numeric constants, and driver names are allowed"
            )
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise UnderwritingCalculationError(
                    f"Driver '{name}' may only reference numeric constants"
                )
            # A literal like 1e999 parses as float infinity: unguarded it yields a silent null
            # value, and inf/inf raises decimal.InvalidOperation (an HTTP 500) at evaluation.
            if not math.isfinite(node.value):
                raise UnderwritingCalculationError(
                    f"Driver '{name}' contains a non-finite constant; "
                    "literals must be finite numbers"
                )
        if isinstance(node, ast.Name):
            references.add(node.id)
    return tree, references


def _evaluate_driver_node(node: ast.AST, values: dict[str, Decimal]) -> Decimal:
    """Decimal-exact evaluation of a pre-validated driver AST."""
    if isinstance(node, ast.Expression):
        return _evaluate_driver_node(node.body, values)
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_driver_node(node.operand, values)
        return operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp):
        left = _evaluate_driver_node(node.left, values)
        right = _evaluate_driver_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise UnderwritingCalculationError("Division by zero in a driver formula")
        return left / right
    raise UnderwritingCalculationError("Unsupported driver expression")  # pragma: no cover


def calculate_driver_model(request: DriverModelRequest) -> DriverModelResult:
    """Validate, cycle-check, topologically evaluate user drivers with provenance on each line."""
    definitions = {driver.name: driver for driver in request.drivers}
    parsed: dict[str, ast.Expression] = {}
    dependencies: dict[str, list[str]] = {}
    for driver in request.drivers:
        tree, references = _parse_driver_formula(driver.name, driver.formula)
        unknown = sorted(ref for ref in references if ref not in definitions)
        if unknown:
            raise UnderwritingCalculationError(
                f"Driver '{driver.name}' references unknown driver(s): {', '.join(unknown)}"
            )
        parsed[driver.name] = tree
        dependencies[driver.name] = sorted(references)

    # Depth-first topological sort that names the exact cycle path when one exists.
    state: dict[str, int] = {}  # 0/absent = unvisited, 1 = on stack, 2 = done
    order: list[str] = []
    stack: list[str] = []

    def visit(name: str) -> None:
        state[name] = 1
        stack.append(name)
        for dep in dependencies[name]:
            if state.get(dep, 0) == 1:
                cycle = stack[stack.index(dep):] + [dep]
                raise UnderwritingCalculationError(
                    "Driver formulas contain a cycle: " + " -> ".join(cycle)
                )
            if state.get(dep, 0) == 0:
                visit(dep)
        stack.pop()
        state[name] = 2
        order.append(name)

    for driver in request.drivers:
        if state.get(driver.name, 0) == 0:
            visit(driver.name)

    values: dict[str, Decimal] = {}
    transitive: dict[str, set[str]] = {}
    for name in order:
        value = _evaluate_driver_node(parsed[name], values)
        try:
            representable = math.isfinite(float(value))
        except OverflowError:
            representable = False
        if not representable:
            raise UnderwritingCalculationError(
                f"Driver '{name}' evaluates to a value outside the representable numeric range"
            )
        values[name] = value
        closure: set[str] = set(dependencies[name])
        for dep in dependencies[name]:
            closure |= transitive[dep]
        transitive[name] = closure

    resolved = [
        {
            "name": driver.name,
            "value": _ratio(float(values[driver.name])),
            "formula": driver.formula,
            "unit": driver.unit,
            "depends_on": dependencies[driver.name],
            "provenance": {
                "note": driver.provenance,
                "inputs": sorted(transitive[driver.name]),
            },
        }
        for driver in request.drivers
    ]
    return DriverModelResult.model_validate(
        {"resolved": resolved, "evaluation_order": order}
    )


# --- G25 Working-capital seasonality ----------------------------------------------------------


def calculate_working_capital_seasonality(
    request: WorkingCapitalSeasonalityRequest,
) -> WorkingCapitalSeasonalityResult:
    """Peg working capital per calendar month (never a single annual average).

    Months with observations are averaged in place; absent months are reported as missing and are
    never imputed or interpolated. The seasonal swing (peak/trough/amplitude) is measured over the
    present months only.
    """
    by_month: dict[int, list[float]] = {}
    for row in request.monthly_working_capital:
        by_month.setdefault(row.month, []).append(row.value)

    monthly_pegs = [
        {
            "month": month,
            "peg": _money(statistics.fmean(by_month[month])),
            "observation_count": len(by_month[month]),
        }
        for month in sorted(by_month)
    ]
    missing_months = [month for month in range(1, 13) if month not in by_month]
    peak = max(monthly_pegs, key=lambda entry: entry["peg"])
    trough = min(monthly_pegs, key=lambda entry: entry["peg"])
    return WorkingCapitalSeasonalityResult.model_validate(
        {
            "status": "complete" if not missing_months else "partial",
            "monthly_pegs": monthly_pegs,
            "present_months": sorted(by_month),
            "missing_months": missing_months,
            "annual_average": _money(statistics.fmean(entry["peg"] for entry in monthly_pegs)),
            "peak_month": peak["month"],
            "trough_month": trough["month"],
            "amplitude": _money(peak["peg"] - trough["peg"]),
        }
    )


# --- G26 Dividend recap + bolt-on acquisition events -------------------------------------------


def calculate_recap_boltons(request: RecapBoltOnRequest) -> RecapBoltOnResult:
    """Overlay dividend-recap and bolt-on events on the base case and re-derive sponsor returns.

    A dividend recap draws incremental debt to pay an equity dividend (returns capital early, so it
    lifts IRR and raises exit leverage while leaving nominal MoIC roughly flat). A bolt-on adds
    EBITDA funded by debt or fresh equity. Exit-state deltas and the interim cash-flow timeline are
    accumulated in Decimal so the per-event sources/uses reconcile exactly, then the existing
    ``xirr`` machinery re-prices the sponsor's dated cash flows.
    """
    assumptions = request.assumptions
    sources_uses = calculate_sources_uses(assumptions)
    projection = calculate_projection(assumptions)
    base_returns = calculate_returns(assumptions, sources_uses, projection)

    end_by_label = {period["label"]: period["end_date"] for period in projection}
    final = projection[-1]

    cent = Decimal("0.01")
    ownership = Decimal(str(sources_uses["sponsor_ownership"]))
    invested = Decimal(str(sources_uses["sponsor_equity"]))
    exit_multiple = Decimal(str(assumptions.transaction.exit_multiple))
    exit_ebitda_base = Decimal(str(final["ebitda"])) / Decimal(str(final["year_fraction"]))
    exit_debt_base = Decimal(str(base_returns["exit_debt"]))
    exit_cash_base = Decimal(str(base_returns["exit_cash"]))

    exit_ebitda_delta = Decimal("0")
    exit_debt_delta = Decimal("0")
    interim_flows: list[tuple[date, Decimal]] = []
    events_out: list[dict] = []
    all_balanced = True

    for event in request.events:
        if event.period not in end_by_label:
            raise UnderwritingCalculationError(
                f"Event period '{event.period}' is not a projection period"
            )
        event_date = end_by_label[event.period]
        if event.type == "dividend_recap":
            amount = Decimal(str(event.amount))
            exit_debt_delta += amount
            interim_flows.append((event_date, (amount * ownership).quantize(cent)))
            sources = [{"name": "Incremental debt", "amount": _money(float(amount))}]
            uses = [{"name": "Equity dividend", "amount": _money(float(amount))}]
        else:
            inc_ebitda = Decimal(str(event.incremental_ebitda))
            purchase = (inc_ebitda * Decimal(str(event.multiple_paid))).quantize(cent)
            exit_ebitda_delta += inc_ebitda
            if event.funded_by == "debt":
                exit_debt_delta += purchase
                sources = [{"name": "Acquisition debt", "amount": _money(float(purchase))}]
            else:
                interim_flows.append((event_date, (-purchase * ownership).quantize(cent)))
                sources = [{"name": "Sponsor equity", "amount": _money(float(purchase))}]
            uses = [{"name": "Bolt-on purchase price", "amount": _money(float(purchase))}]
        balanced = sum(
            (Decimal(str(line["amount"])) for line in sources), Decimal("0")
        ) == sum((Decimal(str(line["amount"])) for line in uses), Decimal("0"))
        all_balanced = all_balanced and balanced
        events_out.append(
            {
                "type": event.type,
                "period": event.period,
                "sources": sources,
                "uses": uses,
                "balanced": balanced,
            }
        )

    new_exit_ebitda = exit_ebitda_base + exit_ebitda_delta
    new_exit_debt = exit_debt_base + exit_debt_delta
    new_exit_equity = (exit_multiple * new_exit_ebitda - new_exit_debt + exit_cash_base).quantize(
        cent
    )
    sponsor_exit = (new_exit_equity * ownership).quantize(cent)

    cash_flows: list[tuple[date, Decimal]] = [
        (assumptions.transaction.close_date, -invested),
        *interim_flows,
        (final["end_date"], sponsor_exit),
    ]
    inflows = sum((amount for _, amount in cash_flows if amount > 0), Decimal("0"))
    outflows = sum((-amount for _, amount in cash_flows if amount < 0), Decimal("0"))
    adjusted_moic = _ratio(float(inflows / outflows)) if outflows > 0 else None
    adjusted_irr = xirr([(flow_date, float(amount)) for flow_date, amount in cash_flows])

    base_leverage = (
        _ratio(float(exit_debt_base / exit_ebitda_base)) if exit_ebitda_base > 0 else None
    )
    adjusted_leverage = (
        _ratio(float(new_exit_debt / new_exit_ebitda)) if new_exit_ebitda > 0 else None
    )

    def _delta(after: float | None, before: float | None) -> float | None:
        return _ratio(after - before) if after is not None and before is not None else None

    return RecapBoltOnResult.model_validate(
        {
            "base": {
                "irr": base_returns["xirr"],
                "moic": base_returns["moic"],
                "exit_debt": base_returns["exit_debt"],
                "exit_ebitda": _money(float(exit_ebitda_base)),
                "exit_equity_value": base_returns["exit_equity_value"],
                "exit_leverage": base_leverage,
                "sponsor_exit_proceeds": base_returns["sponsor_exit_proceeds"],
                "sponsor_invested_capital": base_returns["sponsor_invested_capital"],
                "cash_flows": base_returns["cash_flows"],
            },
            "adjusted": {
                "irr": adjusted_irr,
                "moic": adjusted_moic,
                "exit_debt": _money(float(new_exit_debt)),
                "exit_ebitda": _money(float(new_exit_ebitda)),
                "exit_equity_value": _money(float(new_exit_equity)),
                "exit_leverage": adjusted_leverage,
                "sponsor_exit_proceeds": _money(float(sponsor_exit)),
                "sponsor_invested_capital": _money(float(invested)),
                "cash_flows": [
                    {"date": flow_date.isoformat(), "amount": _money(float(amount))}
                    for flow_date, amount in cash_flows
                ],
            },
            "events": events_out,
            "irr_delta": _delta(adjusted_irr, base_returns["xirr"]),
            "moic_delta": _delta(adjusted_moic, base_returns["moic"]),
            "leverage_delta": _delta(adjusted_leverage, base_leverage),
            "sources_uses_balanced": all_balanced,
            "generated_at": now_utc(),
        }
    )


# --- G69 One-way sensitivity tornado ----------------------------------------------------------
#
# Shift conventions per variable (documented on the request schema as well):
#   entry_multiple / exit_multiple  -> "relative": low/high = base x (1 -/+ relative_shift)
#   *_shift variables               -> "absolute": low/high = -/+ absolute_shift around a base
#                                      of zero, where a relative shift is meaningless.

_TORNADO_VARIABLES: tuple[str, ...] = (
    "entry_multiple",
    "exit_multiple",
    "base_rate_shift",
    "revenue_growth_shift",
    "ebitda_margin_shift",
)
_TORNADO_CONVENTIONS: dict[str, str] = {
    "entry_multiple": "relative",
    "exit_multiple": "relative",
    "base_rate_shift": "absolute",
    "revenue_growth_shift": "absolute",
    "ebitda_margin_shift": "absolute",
}


def _returns_metric_with_wipeout(returns: dict, metric: str) -> float | None:
    """IRR or MoIC from a returns dict, applying the G21 wipeout discipline.

    When sponsor exit proceeds are <= 0 no IRR solves, but the outcome is a total loss: it reads
    as -100%, never as a silently missing value.
    """
    irr, moic = returns["xirr"], returns["moic"]
    if irr is None and moic is not None and returns["sponsor_exit_proceeds"] <= 0:
        irr = -1.0
    return irr if metric == "irr" else moic


def _tornado_point(
    assumptions: UnderwritingAssumptions, variable: str, value: float, metric: str
) -> tuple[float | None, str | None]:
    """Evaluate one tornado extreme; returns (metric value, failure reason)."""
    try:
        scenario = _apply_variable(assumptions, variable, value)
        sources_uses = calculate_sources_uses(scenario)
        projection = calculate_projection(scenario)
        returns = calculate_returns(scenario, sources_uses, projection)
    except UnderwritingCalculationError as exc:
        return None, str(exc)
    result = _returns_metric_with_wipeout(returns, metric)
    if result is None:
        return None, f"{metric} did not converge at {variable}={value:g}"
    return result, None


def calculate_sensitivity_tornado(payload: SensitivityTornadoRequest) -> SensitivityTornadoResult:
    """Rank every sensitivity driver's one-way impact on IRR/MoIC around the base case.

    Rows are ranked by max(|delta_low|, |delta_high|) descending. An extreme whose evaluation
    raises ``UnderwritingCalculationError`` (or whose metric does not converge) makes the row
    ``evaluable=False`` with the named reason — rows are NEVER dropped silently. Inevaluable rows
    sort after every evaluable row, in vocabulary order.
    """
    assumptions = payload.assumptions
    base_sources_uses = calculate_sources_uses(assumptions)
    base_projection = calculate_projection(assumptions)
    base_returns = calculate_returns(assumptions, base_sources_uses, base_projection)
    base_metric = _returns_metric_with_wipeout(base_returns, payload.metric)
    if base_metric is None:
        raise UnderwritingCalculationError(
            f"The base case does not produce a convergent {payload.metric}"
        )

    variables = payload.variables or list(_TORNADO_VARIABLES)
    rows: list[dict] = []
    for variable in variables:
        convention = _TORNADO_CONVENTIONS[variable]
        if convention == "relative":
            base_value = getattr(assumptions.transaction, variable)
            low_value = base_value * (1.0 - payload.relative_shift)
            high_value = base_value * (1.0 + payload.relative_shift)
        else:
            base_value = 0.0
            low_value = -payload.absolute_shift
            high_value = payload.absolute_shift
        metric_low, low_reason = _tornado_point(assumptions, variable, low_value, payload.metric)
        metric_high, high_reason = _tornado_point(assumptions, variable, high_value, payload.metric)
        delta_low = _ratio(metric_low - base_metric) if metric_low is not None else None
        delta_high = _ratio(metric_high - base_metric) if metric_high is not None else None
        evaluable = low_reason is None and high_reason is None
        reasons = [
            f"{side} extreme ({value:g}): {reason}"
            for side, value, reason in (
                ("low", low_value, low_reason),
                ("high", high_value, high_reason),
            )
            if reason is not None
        ]
        rows.append(
            {
                "variable": variable,
                "convention": convention,
                "base_value": _ratio(base_value),
                "low_value": _ratio(low_value),
                "high_value": _ratio(high_value),
                "metric_low": metric_low,
                "metric_high": metric_high,
                "delta_low": delta_low,
                "delta_high": delta_high,
                "max_abs_delta": (
                    _ratio(max(abs(delta_low), abs(delta_high))) if evaluable else None
                ),
                "evaluable": evaluable,
                "reason": "; ".join(reasons) if reasons else None,
            }
        )

    order = {variable: index for index, variable in enumerate(variables)}
    ranked = sorted(
        rows,
        key=lambda row: (
            (0, -row["max_abs_delta"], order[row["variable"]])
            if row["evaluable"]
            else (1, 0.0, order[row["variable"]])
        ),
    )
    return SensitivityTornadoResult.model_validate(
        {
            "metric": payload.metric,
            "base_metric": base_metric,
            "relative_shift": payload.relative_shift,
            "absolute_shift": payload.absolute_shift,
            "rows": ranked,
        }
    )


# --- G70 Dividend recap solver ----------------------------------------------------------------

_RECAP_CONSTRAINT_ORDER: tuple[str, ...] = (
    "max_total_leverage",
    "min_interest_coverage",
    "min_fixed_charge_coverage",
    "min_liquidity",
)


def _recap_constraint_statuses(payload: DividendRecapSolveRequest, rows: list[dict]) -> list[dict]:
    """Evaluate the requested constraints over the affected projection rows.

    Coverage constraints treat a period with no cash interest / fixed charges as trivially
    satisfied (there is nothing to cover — the exit-readiness debt-free discipline). A period
    where total leverage is UNDEFINED (non-positive EBITDA) fails the leverage constraint
    closed: compliance that cannot be computed is never presumed.
    """
    statuses: list[dict] = []

    def status(name, threshold, actual, binding_period, headroom, satisfied, note=None):
        statuses.append(
            {
                "name": name,
                "threshold": _ratio(threshold),
                "actual": _ratio(actual) if actual is not None else None,
                "binding_period": binding_period,
                "headroom": _ratio(headroom) if headroom is not None else None,
                "satisfied": satisfied,
                "note": note,
            }
        )

    if payload.max_total_leverage is not None:
        threshold = payload.max_total_leverage
        worst = worst_period = missing = None
        for row in rows:
            value = row["total_leverage"]
            if value is None:
                if missing is None:
                    missing = row["label"]
            elif worst is None or value > worst:
                worst, worst_period = value, row["label"]
        if missing is not None:
            status(
                "max_total_leverage", threshold, worst, missing, None, False,
                f"total leverage is undefined in {missing} (non-positive EBITDA); "
                "compliance cannot be verified",
            )
        else:
            status(
                "max_total_leverage", threshold, worst, worst_period,
                threshold - worst, worst <= threshold + _EPSILON,
            )

    for name, key, charge in (
        ("min_interest_coverage", "interest_coverage", "cash interest"),
        ("min_fixed_charge_coverage", "fixed_charge_coverage", "fixed charges"),
    ):
        threshold = getattr(payload, name)
        if threshold is None:
            continue
        worst = worst_period = None
        for row in rows:
            value = row[key]
            if value is not None and (worst is None or value < worst):
                worst, worst_period = value, row["label"]
        if worst is None:
            status(
                name, threshold, None, None, None, True,
                f"no {charge} in any evaluated period; coverage is trivially satisfied",
            )
        else:
            status(
                name, threshold, worst, worst_period,
                worst - threshold, worst + _EPSILON >= threshold,
            )

    if payload.min_liquidity is not None:
        threshold = payload.min_liquidity
        worst = worst_period = None
        for row in rows:
            value = row["liquidity"]
            if worst is None or value < worst:
                worst, worst_period = value, row["label"]
        status(
            "min_liquidity", threshold, worst, worst_period,
            worst - threshold, worst + _EPSILON >= threshold,
        )
    return statuses


def solve_dividend_recap(payload: DividendRecapSolveRequest) -> DividendRecapSolveResult:
    """Maximum special distribution at the end of ``payload.period``, solved by bisection.

    The distribution flows through the period's cash waterfall via the ``special_distributions``
    seam (the committed revolver may fund it), and constraints are evaluated at the distribution
    period and every LATER period. Outcomes:

    - ``solved``: ``binding_constraint`` is the constraint violated just beyond the maximum (the
      limit that becomes tight); ``constraints`` reports every constraint AT the maximum.
    - ``infeasible``: a constraint is violated with zero distribution — it is named.
    - ``unbounded``: no constraint tightens up to a probed bound far beyond the enterprise value
      (e.g. leverage-only constraints once the revolver is exhausted) — reported explicitly,
      never returned as a fabricated large number.
    """
    assumptions = payload.assumptions
    sources_uses = calculate_sources_uses(assumptions)
    base_projection = calculate_projection(assumptions)
    labels = [row["label"] for row in base_projection]
    if payload.period not in labels:
        raise UnderwritingCalculationError(
            f"Distribution period '{payload.period}' is not a projection period"
        )
    start_index = labels.index(payload.period)

    def evaluate(amount: float) -> tuple[list[dict], bool]:
        projection = (
            base_projection
            if amount == 0.0
            else calculate_projection(
                assumptions, special_distributions={payload.period: amount}
            )
        )
        statuses = _recap_constraint_statuses(payload, projection[start_index:])
        return statuses, all(item["satisfied"] for item in statuses)

    def result(**overrides) -> DividendRecapSolveResult:
        base = {
            "period": payload.period,
            "max_distribution": None,
            "sponsor_share": None,
            "binding_constraint": None,
            "iterations": 0,
            "note": None,
        }
        base.update(overrides)
        return DividendRecapSolveResult.model_validate(base)

    zero_statuses, zero_feasible = evaluate(0.0)
    if not zero_feasible:
        violated = next(item for item in zero_statuses if not item["satisfied"])
        return result(
            status="infeasible",
            binding_constraint=violated["name"],
            constraints=zero_statuses,
            note=(
                f"Constraint {violated['name']} is already violated with no distribution; "
                "no recap is possible"
            ),
        )

    upper = max(payload.tolerance * 10.0, sources_uses["entry_enterprise_value"])
    upper_statuses, upper_feasible = evaluate(upper)
    doublings = 0
    while upper_feasible and doublings < 40:
        upper *= 2.0
        doublings += 1
        upper_statuses, upper_feasible = evaluate(upper)
    if upper_feasible:
        return result(
            status="unbounded",
            constraints=upper_statuses,
            note=(
                "No requested constraint becomes binding for distributions up to "
                f"{_money(upper)}; the maximum is unbounded under these constraints"
            ),
        )

    low, low_statuses = 0.0, zero_statuses
    high, high_statuses = upper, upper_statuses
    iterations = 0
    for iteration in range(1, payload.max_iterations + 1):
        if high - low <= payload.tolerance:
            break
        midpoint = (low + high) / 2.0
        statuses, feasible = evaluate(midpoint)
        iterations = iteration
        if feasible:
            low, low_statuses = midpoint, statuses
        else:
            high, high_statuses = midpoint, statuses
    binding = next(item["name"] for item in high_statuses if not item["satisfied"])
    return result(
        status="solved",
        max_distribution=_money(low),
        sponsor_share=_money(low * sources_uses["sponsor_ownership"]),
        binding_constraint=binding,
        constraints=low_statuses,
        iterations=iterations,
    )


# --- G71 Working-capital facility sizing ------------------------------------------------------


def _annual_groups(projection: list[dict]) -> list[list[dict]]:
    """Group projection rows into consecutive 12-month hold years.

    A trailing partial year (a hold that is not a whole number of years) is kept as a shorter
    final group. A single period that straddles a 12-month boundary cannot be assigned to a
    year and fails closed.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    months = 0
    for row in projection:
        if months + row["months"] > 12:
            raise UnderwritingCalculationError(
                f"Projection period '{row['label']}' straddles a 12-month year boundary; "
                "annual analyses require year-aligned periods"
            )
        current.append(row)
        months += row["months"]
        if months == 12:
            groups.append(current)
            current, months = [], 0
    if current:
        groups.append(current)
    return groups


def calculate_facility_sizing(payload: FacilitySizingRequest) -> FacilitySizingResult:
    """Size the revolver against peak intra-year working-capital needs per projection year.

    The G25 seasonality model supplies the monthly shape; each projection year's month-m working
    capital is the seasonal peg SCALED PROPORTIONALLY to that year's modeled annual level
    (``peg_m x annual_nwc / seasonal_annual_average``). The peak draw is the excess of the peak
    month over the annual level already funded by the projection; headroom nets the revolver
    balance the annual model has already drawn. Negative headroom = the facility is undersized.

    No monthly observations -> explicit ``unavailable`` (a flat profile is never fabricated).
    A year whose annual working capital is not positive cannot be proportionally scaled and is
    reported ``evaluable=False`` with the reason, never imputed.
    """
    assumptions = payload.assumptions
    modeled_commitment = sum(
        tranche.commitment or 0.0
        for tranche in assumptions.debt_tranches
        if tranche.tranche_type == "revolver"
    )
    override = payload.commitment_override
    commitment = modeled_commitment if override is None else override
    commitment_source = "modeled_revolvers" if override is None else "override"

    if not payload.monthly_working_capital:
        return FacilitySizingResult.model_validate(
            {
                "status": "unavailable",
                "reason": (
                    "No monthly working-capital observations were provided; intra-year "
                    "facility needs cannot be sized from a fabricated flat profile"
                ),
                "seasonality_missing_months": list(range(1, 13)),
                "seasonal_annual_average": None,
                "seasonal_peak_month": None,
                "commitment": _money(commitment),
                "commitment_source": commitment_source,
                "years": [],
                "peak_year_label": None,
                "peak_draw": None,
            }
        )

    seasonality = calculate_working_capital_seasonality(
        WorkingCapitalSeasonalityRequest(monthly_working_capital=payload.monthly_working_capital)
    )
    average = seasonality.annual_average
    if average <= 0:
        raise UnderwritingCalculationError(
            "The seasonal annual average must be positive to scale the monthly profile onto "
            "projected working capital"
        )
    peak_peg = max(entry.peg for entry in seasonality.monthly_pegs)
    projection = calculate_projection(assumptions)
    revolver_names = {
        tranche.name
        for tranche in assumptions.debt_tranches
        if tranche.tranche_type == "revolver"
    }

    years: list[dict] = []
    for index, group in enumerate(_annual_groups(projection)):
        last = group[-1]
        annual_nwc = last["net_working_capital"]
        existing_draw = _money(
            sum(
                row["ending_balance"]
                for row in last["debt_tranches"]
                if row["name"] in revolver_names
            )
        )
        year = {
            "year_label": f"Y{index + 1}",
            "period_label": last["label"],
            "months": sum(row["months"] for row in group),
            "annual_nwc": annual_nwc,
            "evaluable": True,
            "reason": None,
            "peak_month": None,
            "peak_monthly_nwc": None,
            "peak_draw": None,
            "existing_revolver_draw": existing_draw,
            "commitment": _money(commitment),
            "headroom": None,
        }
        if annual_nwc <= 0:
            year["evaluable"] = False
            year["reason"] = (
                "annual net working capital is not positive; the seasonal profile cannot be "
                "proportionally scaled"
            )
        else:
            raw_peak = peak_peg * annual_nwc / average
            peak_draw = _money(max(0.0, raw_peak - annual_nwc))
            year["peak_month"] = seasonality.peak_month
            year["peak_monthly_nwc"] = _money(raw_peak)
            year["peak_draw"] = peak_draw
            year["headroom"] = _money(commitment - existing_draw - peak_draw)
        years.append(year)

    evaluable = [year for year in years if year["evaluable"]]
    peak_year = max(evaluable, key=lambda year: year["peak_draw"]) if evaluable else None
    return FacilitySizingResult.model_validate(
        {
            "status": seasonality.status,
            "reason": None,
            "seasonality_missing_months": seasonality.missing_months,
            "seasonal_annual_average": seasonality.annual_average,
            "seasonal_peak_month": seasonality.peak_month,
            "commitment": _money(commitment),
            "commitment_source": commitment_source,
            "years": years,
            "peak_year_label": peak_year["year_label"] if peak_year else None,
            "peak_draw": peak_year["peak_draw"] if peak_year else None,
        }
    )


# --- G72 Fund-level Monte Carlo ---------------------------------------------------------------
#
# Factor-to-driver mapping: each macro factor draw, scaled by the deal's loading, is applied
# through the SAME ``_apply_variable`` vocabulary the sensitivity/MC machinery uses:
#   rate_shift     -> base_rate_shift        (additive)
#   growth_shift   -> revenue_growth_shift   (additive)
#   multiple_shift -> exit_multiple          (additive turns on the deal's exit multiple)

_FUND_FACTOR_VARIABLES: dict[str, str] = {
    "rate_shift": "base_rate_shift",
    "growth_shift": "revenue_growth_shift",
    "multiple_shift": "exit_multiple",
}


def _resolve_fund_deals(
    session: Session, payload: FundMonteCarloRequest
) -> tuple[list[FundDealSpec], list[dict]]:
    """Resolve a saved fund construction into simulation deals (G29 sizing discipline).

    A deal enters the simulation only when its sizing case (base-case preference) carries both
    committed sponsor equity and a valid set of underwriting assumptions; everything else is
    EXCLUDED with a named reason, never imputed.
    """
    from src.models.deal_workflow import Fund
    from src.models.underwriting_model import UnderwritingCaseVersion
    from src.services import portfolio_service

    fund = session.get(Fund, payload.fund_id)
    if fund is None:
        raise NotFound(f"Fund '{payload.fund_id}' not found")
    deals = sorted(
        session.scalars(select(Deal).where(Deal.fund_id == fund.id)),
        key=lambda deal: deal.code,
    )
    workspace_ids = [deal.workspace_id for deal in deals if deal.workspace_id]
    cases_by_workspace: dict[str, list] = {}
    if workspace_ids:
        for case in session.scalars(
            select(UnderwritingCaseVersion).where(
                UnderwritingCaseVersion.workspace_id.in_(workspace_ids)
            )
        ):
            cases_by_workspace.setdefault(case.workspace_id, []).append(case)

    resolved: list[FundDealSpec] = []
    excluded: list[dict] = []
    for deal in deals:
        case = portfolio_service._pick_sizing_case(
            cases_by_workspace.get(deal.workspace_id or "", [])
        )
        if case is None:
            excluded.append({"code": deal.code, "reason": "no underwriting case"})
            continue
        committed = portfolio_service._committed_capital(case)
        if committed is None or committed <= 0:
            excluded.append(
                {"code": deal.code, "reason": "case carries no committed sponsor equity"}
            )
            continue
        try:
            assumptions = UnderwritingAssumptions.model_validate(case.assumptions)
        except ValidationError:
            excluded.append(
                {"code": deal.code, "reason": "case assumptions are not a valid underwriting model"}
            )
            continue
        resolved.append(
            FundDealSpec(
                name=deal.code,
                assumptions=assumptions,
                commitment=committed,
                loadings=dict(payload.fund_deal_loadings),
            )
        )
    if not resolved:
        raise UnderwritingCalculationError(
            f"Fund '{fund.name}' has no deals with usable underwriting cases"
        )
    return resolved, excluded


def run_fund_monte_carlo(
    payload: FundMonteCarloRequest, session: Session | None = None
) -> FundMonteCarloResult:
    """Simulate the fund: shared macro factor draws plus per-deal idiosyncratic draws.

    One seeded ``random.Random`` materializes EVERY draw up front (per iteration: factors in
    request order, then each deal's idiosyncratic draws in order), so the correlated run and the
    zero-loadings independent re-run consume byte-identical randomness — the reported
    ``correlation_effect`` isolates the loadings, not sampling noise.

    Per iteration the fund outcome is the commitment-weighted MoIC and the pooled XIRR of every
    deal's dated sponsor cash flows (scaled by ``commitment / sponsor_equity``). G21 wipeout
    discipline applies at both levels: a wiped-out deal enters with its computed MoIC and -100%
    IRR, and a fund whose pooled inflows are non-positive reads as a -100% total loss. An
    iteration where any deal's assumptions become invalid, or any metric fails to converge, is
    counted ``failed`` for the whole fund so the sample stays coherent across deals.
    """
    if payload.fund_id is not None:
        if session is None:  # pragma: no cover - the router always passes a session
            raise UnderwritingCalculationError(
                "A database session is required to resolve a fund's deals"
            )
        deals, excluded = _resolve_fund_deals(session, payload)
        source = "fund_construction"
    else:
        deals, excluded, source = list(payload.deals), [], "request"

    commitments: list[float] = []
    invested_base: list[float] = []
    scales: list[float] = []
    for deal in deals:
        try:
            invested = calculate_sources_uses(deal.assumptions)["sponsor_equity"]
        except UnderwritingCalculationError as exc:
            raise UnderwritingCalculationError(f"Deal '{deal.name}': {exc}") from exc
        if invested <= 0:
            raise UnderwritingCalculationError(
                f"Deal '{deal.name}' has no positive sponsor equity to scale fund cash flows"
            )
        commitment = deal.commitment if deal.commitment is not None else invested
        commitments.append(commitment)
        invested_base.append(invested)
        scales.append(commitment / invested)
    total_commitment = sum(commitments)

    rng = random.Random(payload.seed)
    factor_samples: dict[str, list[float]] = {factor.name: [] for factor in payload.factors}
    draw_plan: list[tuple[dict[str, float], list[list[tuple[str, float]]]]] = []
    for _ in range(payload.iterations):
        factor_draws: dict[str, float] = {}
        for factor in payload.factors:
            value = _sample_driver(rng, factor)
            factor_draws[factor.name] = value
            factor_samples[factor.name].append(value)
        idiosyncratic = [
            [
                (distribution.driver, _sample_driver(rng, distribution))
                for distribution in deal.distributions
            ]
            for deal in deals
        ]
        draw_plan.append((factor_draws, idiosyncratic))

    def simulate(apply_loadings: bool) -> dict:
        fund_irr_values: list[float] = []
        fund_moic_values: list[float] = []
        deal_irr_values: list[list[float]] = [[] for _ in deals]
        deal_moic_values: list[list[float]] = [[] for _ in deals]
        failed = 0
        for factor_draws, idiosyncratic in draw_plan:
            pooled_flows: list[tuple[date, float]] = []
            weighted_moic = 0.0
            per_deal: list[tuple[float, float]] = []
            iteration_ok = True
            try:
                for index, deal in enumerate(deals):
                    scenario = deal.assumptions
                    for driver, value in idiosyncratic[index]:
                        scenario = _apply_variable(scenario, driver, value)
                    if apply_loadings:
                        for factor_name, draw in factor_draws.items():
                            shift = deal.loadings.get(factor_name, 1.0) * draw
                            if shift == 0.0:
                                continue
                            variable = _FUND_FACTOR_VARIABLES[factor_name]
                            if variable in {"entry_multiple", "exit_multiple"}:
                                scenario = _apply_variable(
                                    scenario,
                                    variable,
                                    getattr(scenario.transaction, variable) + shift,
                                )
                            else:
                                scenario = _apply_variable(scenario, variable, shift)
                    sources_uses = calculate_sources_uses(scenario)
                    projection = calculate_projection(scenario)
                    returns = calculate_returns(scenario, sources_uses, projection)
                    irr = _returns_metric_with_wipeout(returns, "irr")
                    moic = returns["moic"]
                    if irr is None or moic is None:
                        iteration_ok = False
                        break
                    per_deal.append((irr, moic))
                    weighted_moic += commitments[index] * moic
                    pooled_flows.append(
                        (
                            scenario.transaction.close_date,
                            -returns["sponsor_invested_capital"] * scales[index],
                        )
                    )
                    pooled_flows.append(
                        (
                            projection[-1]["end_date"],
                            returns["sponsor_exit_proceeds"] * scales[index],
                        )
                    )
            except UnderwritingCalculationError:
                iteration_ok = False
            if not iteration_ok:
                failed += 1
                continue
            fund_moic = weighted_moic / total_commitment
            fund_irr = xirr(pooled_flows)
            if fund_irr is None:
                if sum(amount for _, amount in pooled_flows if amount > 0) <= 0:
                    fund_irr = -1.0  # fund-level wipeout: a total loss, not a failed iteration
                else:
                    failed += 1
                    continue
            fund_irr_values.append(fund_irr)
            fund_moic_values.append(fund_moic)
            for index, (irr, moic) in enumerate(per_deal):
                deal_irr_values[index].append(irr)
                deal_moic_values[index].append(moic)
        if not fund_irr_values:
            raise UnderwritingCalculationError(
                "No fund Monte Carlo iteration produced a converged fund IRR and MoIC"
                + ("" if apply_loadings else " in the independent (zero-loadings) re-run")
            )
        converged = len(fund_irr_values)
        return {
            "converged": converged,
            "failed": failed,
            "fund_irr": _metric_band(fund_irr_values),
            "fund_moic": _metric_band(fund_moic_values),
            "probability_fund_moic_below_1": _ratio(
                sum(1 for value in fund_moic_values if value < 1.0) / converged
            ),
            "deal_irr": deal_irr_values,
            "deal_moic": deal_moic_values,
        }

    correlated = simulate(apply_loadings=True)
    independent = simulate(apply_loadings=False)

    deals_out = [
        {
            "name": deal.name,
            "commitment": _money(commitments[index]),
            "base_invested": _money(invested_base[index]),
            "irr": _metric_band(correlated["deal_irr"][index]),
            "moic": _metric_band(correlated["deal_moic"][index]),
            "probability_moic_below_1": _ratio(
                sum(1 for value in correlated["deal_moic"][index] if value < 1.0)
                / len(correlated["deal_moic"][index])
            ),
        }
        for index, deal in enumerate(deals)
    ]
    return FundMonteCarloResult.model_validate(
        {
            "iterations": payload.iterations,
            "seed": payload.seed,
            "converged": correlated["converged"],
            "failed": correlated["failed"],
            "source": source,
            "fund_id": payload.fund_id,
            "excluded_deals": excluded,
            "total_commitment": _money(total_commitment),
            "fund_irr": correlated["fund_irr"],
            "fund_moic": correlated["fund_moic"],
            "probability_fund_moic_below_1": correlated["probability_fund_moic_below_1"],
            "deals": deals_out,
            "factor_summaries": [
                {
                    "name": factor.name,
                    "kind": factor.kind,
                    "sampled_mean": _ratio(statistics.fmean(factor_samples[factor.name])),
                    "sampled_min": _ratio(min(factor_samples[factor.name])),
                    "sampled_max": _ratio(max(factor_samples[factor.name])),
                }
                for factor in payload.factors
            ],
            "correlation_effect": {
                "independent_converged": independent["converged"],
                "independent_failed": independent["failed"],
                "independent_irr": independent["fund_irr"],
                "independent_moic": independent["fund_moic"],
                "independent_probability_fund_moic_below_1": independent[
                    "probability_fund_moic_below_1"
                ],
                "irr_p5_spread": _ratio(
                    correlated["fund_irr"]["p5"] - independent["fund_irr"]["p5"]
                ),
                "irr_p95_spread": _ratio(
                    correlated["fund_irr"]["p95"] - independent["fund_irr"]["p95"]
                ),
                "moic_p5_spread": _ratio(
                    correlated["fund_moic"]["p5"] - independent["fund_moic"]["p5"]
                ),
                "moic_p95_spread": _ratio(
                    correlated["fund_moic"]["p95"] - independent["fund_moic"]["p95"]
                ),
                "note": (
                    "Same seed and draws with every loading zeroed. Negative p5 spreads and "
                    "positive p95 spreads show the shared macro factors widening the fund "
                    "outcome distribution versus independent deals."
                ),
            },
        }
    )


# --- G73 Year-by-year value-creation waterfall ------------------------------------------------


def calculate_annual_value_creation(assumptions: UnderwritingAssumptions) -> AnnualValueCreationResult:
    """Decompose the G22 bridge per hold year with Decimal-exact reconciliation.

    Conventions (matching G22's exact-reconciliation discipline):

    - The valuation multiple stays at ENTRY for every interim year end; the multiple change is
      allocated ENTIRELY to the final year (an interim equity value is a mark, not a sale — the
      exit multiple is only real at exit).
    - The final-year ``multiple_change`` leg IS G22's leg — (exit - entry) x ENTRY EBITDA — so
      the multiple column sums exactly to the total bridge's component.
    - ``ebitda_growth`` is the year's EBITDA change at the entry multiple; ``deleveraging`` is
      the year's net-debt change; ``cross_term`` is the exact residual per year, so each year's
      legs sum EXACTLY to that year's equity-value change and (by telescoping) the years sum
      EXACTLY to the G22 total. For interim years the residual is zero apart from sub-cent leg
      rounding; the final-year residual carries the multiple x EBITDA interaction.
    """
    projection = calculate_projection(assumptions)
    groups = _annual_groups(projection)
    attribution = calculate_returns_attribution(ReturnsAttributionRequest(assumptions=assumptions))

    cent = Decimal("0.01")
    entry_multiple = Decimal(str(assumptions.transaction.entry_multiple))
    exit_multiple = Decimal(str(assumptions.transaction.exit_multiple))
    entry_ebitda = Decimal(str(assumptions.historical.ltm_ebitda))
    entry_net_debt = sum(
        (Decimal(str(tranche.initial_amount)) for tranche in assumptions.debt_tranches),
        Decimal("0"),
    ) - Decimal(str(assumptions.transaction.minimum_cash))
    entry_equity = (entry_multiple * entry_ebitda - entry_net_debt).quantize(cent)

    previous_ebitda = entry_ebitda
    previous_net_debt = entry_net_debt
    previous_equity = entry_equity
    totals: dict[str, Decimal] = {
        "ebitda_growth": Decimal("0"),
        "multiple_change": Decimal("0"),
        "deleveraging": Decimal("0"),
        "cross_term": Decimal("0"),
    }
    years: list[dict] = []
    for index, group in enumerate(groups):
        last = group[-1]
        is_final = index == len(groups) - 1
        applied_multiple = exit_multiple if is_final else entry_multiple
        year_ebitda = Decimal(str(_money(last["ebitda"] / last["year_fraction"])))
        year_net_debt = Decimal(str(last["total_debt"])) - Decimal(str(last["ending_cash"]))
        equity_value = (applied_multiple * year_ebitda - year_net_debt).quantize(cent)
        equity_change = equity_value - previous_equity

        ebitda_growth = (entry_multiple * (year_ebitda - previous_ebitda)).quantize(cent)
        multiple_change = (
            ((exit_multiple - entry_multiple) * entry_ebitda).quantize(cent)
            if is_final
            else Decimal("0.00")
        )
        deleveraging = (previous_net_debt - year_net_debt).quantize(cent)
        cross_term = equity_change - ebitda_growth - multiple_change - deleveraging

        totals["ebitda_growth"] += ebitda_growth
        totals["multiple_change"] += multiple_change
        totals["deleveraging"] += deleveraging
        totals["cross_term"] += cross_term
        years.append(
            {
                "year_label": f"Y{index + 1}",
                "period_label": last["label"],
                "end_date": last["end_date"],
                "months": sum(row["months"] for row in group),
                "applied_multiple": _ratio(float(applied_multiple)),
                "ebitda": float(year_ebitda),
                "net_debt": float(year_net_debt.quantize(cent)),
                "equity_value": float(equity_value),
                "equity_change": float(equity_change),
                "ebitda_growth": float(ebitda_growth),
                "multiple_change": float(multiple_change),
                "deleveraging": float(deleveraging),
                "cross_term": float(cross_term),
                "reconciles": ebitda_growth + multiple_change + deleveraging + cross_term
                == equity_change,
            }
        )
        previous_ebitda = year_ebitda
        previous_net_debt = year_net_debt
        previous_equity = equity_value

    exit_equity = previous_equity
    total = exit_equity - entry_equity
    reconciles = (
        sum(totals.values(), Decimal("0")) == total
        and all(year["reconciles"] for year in years)
    )
    return AnnualValueCreationResult.model_validate(
        {
            "entry_multiple": _ratio(float(entry_multiple)),
            "exit_multiple": _ratio(float(exit_multiple)),
            "entry_ebitda": float(entry_ebitda),
            "entry_net_debt": float(entry_net_debt.quantize(cent)),
            "entry_equity": float(entry_equity),
            "exit_equity": float(exit_equity),
            "total_value_creation": float(total),
            "years": years,
            "totals": {key: float(value) for key, value in totals.items()},
            "matches_attribution_total": Decimal(str(attribution.total_value_creation)) == total,
            "reconciles": reconciles,
        }
    )


def _canonical_hash(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_version(
    session: Session, workspace_id: str, case_key: str
) -> UnderwritingCaseVersion | None:
    return session.scalar(
        select(UnderwritingCaseVersion)
        .where(
            UnderwritingCaseVersion.workspace_id == workspace_id,
            UnderwritingCaseVersion.case_key == case_key,
        )
        .order_by(UnderwritingCaseVersion.version.desc())
        .limit(1)
    )


def _approved_claim_manifest(
    session: Session,
    workspace_id: str,
    claim_ids: list[str],
    actor: ActorContext | None,
) -> list[dict]:
    if not claim_ids:
        return []
    if len(claim_ids) != len(set(claim_ids)):
        raise CaseEvidenceError("approved_claim_ids must be unique")
    deal = session.scalar(select(Deal).where(Deal.workspace_id == workspace_id))
    if deal is None:
        raise CaseEvidenceError(
            "Approved claims can only be bound to a case after the workspace is linked to a deal"
        )
    from src.services import deal_intelligence_service

    try:
        return deal_intelligence_service.approved_claim_manifest(
            session, deal.id, claim_ids, actor
        )
    except deal_intelligence_service.IntelligenceError as exc:
        raise CaseEvidenceError(exc.message, exc.status_code) from exc


def _append_case_version(
    session: Session,
    workspace_id: str,
    payload: UnderwritingCaseCreate,
    actor: ActorContext | None = None,
) -> UnderwritingCaseVersion:
    latest = _latest_version(session, workspace_id, payload.case_key)
    latest_number = latest.version if latest else 0
    if (
        payload.expected_parent_version is not None
        and payload.expected_parent_version != latest_number
    ):
        raise CaseVersionConflict(
            f"Expected parent version {payload.expected_parent_version}, but latest is {latest_number}"
        )
    result = run_underwriting(payload.assumptions)
    input_snapshot = payload.assumptions.model_dump(mode="json")
    claim_manifest = _approved_claim_manifest(
        session, workspace_id, payload.approved_claim_ids, actor
    )
    result_snapshot = result.model_dump(mode="json")
    economic_output = copy.deepcopy(result_snapshot)
    economic_output.pop("generated_at", None)
    record = UnderwritingCaseVersion(
        workspace_id=workspace_id,
        case_key=payload.case_key,
        label=payload.label or payload.case_key.title(),
        version=latest_number + 1,
        parent_version_id=latest.id if latest else None,
        schema_version=SCHEMA_VERSION,
        assumptions=input_snapshot,
        result=result_snapshot,
        approved_claim_ids=list(payload.approved_claim_ids),
        approved_claim_manifest=claim_manifest,
        claim_manifest_hash=_canonical_hash(claim_manifest),
        input_hash=_canonical_hash(
            {"assumptions": input_snapshot, "approved_claim_manifest": claim_manifest}
        ),
        output_hash=_canonical_hash(economic_output),
        created_by=payload.created_by,
        change_note=payload.change_note,
    )
    session.add(record)
    session.flush()
    return record


def create_case_version(
    session: Session,
    workspace_id: str,
    payload: UnderwritingCaseCreate,
    actor: ActorContext | None = None,
) -> UnderwritingCaseVersion:
    get_workspace_or_404(session, workspace_id)
    try:
        record = _append_case_version(session, workspace_id, payload, actor)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CaseVersionConflict(
            "Another editor created this case version first; reload the latest version"
        ) from exc
    except Exception:
        session.rollback()
        raise
    session.refresh(record)
    return record


def create_case_set(
    session: Session,
    workspace_id: str,
    cases: list[UnderwritingCaseCreate],
    actor: ActorContext | None = None,
) -> list[UnderwritingCaseVersion]:
    get_workspace_or_404(session, workspace_id)
    try:
        records = [
            _append_case_version(session, workspace_id, payload, actor) for payload in cases
        ]
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CaseVersionConflict(
            "Another editor created one of these case versions first; reload the case set"
        ) from exc
    except Exception:
        session.rollback()
        raise
    for record in records:
        session.refresh(record)
    return records


def get_case_version(
    session: Session, workspace_id: str, case_key: str, version: int | None = None
) -> UnderwritingCaseVersion:
    get_workspace_or_404(session, workspace_id)
    if version is None:
        record = _latest_version(session, workspace_id, case_key)
    else:
        record = session.scalar(
            select(UnderwritingCaseVersion).where(
                UnderwritingCaseVersion.workspace_id == workspace_id,
                UnderwritingCaseVersion.case_key == case_key,
                UnderwritingCaseVersion.version == version,
            )
        )
    if record is None:
        suffix = "latest" if version is None else f"version {version}"
        raise NotFound(f"Underwriting case '{case_key}' {suffix} not found")
    return record


def list_case_versions(
    session: Session, workspace_id: str, case_key: str | None = None, latest_only: bool = False
) -> list[UnderwritingCaseVersion]:
    get_workspace_or_404(session, workspace_id)
    statement = select(UnderwritingCaseVersion).where(
        UnderwritingCaseVersion.workspace_id == workspace_id
    )
    if case_key is not None:
        statement = statement.where(UnderwritingCaseVersion.case_key == case_key)
    records = list(
        session.scalars(
            statement.order_by(
                UnderwritingCaseVersion.case_key, UnderwritingCaseVersion.version.desc()
            )
        )
    )
    if not latest_only:
        return records
    seen: set[str] = set()
    latest: list[UnderwritingCaseVersion] = []
    for record in records:
        if record.case_key not in seen:
            latest.append(record)
            seen.add(record.case_key)
    return latest


def add_case_decision(
    session: Session,
    workspace_id: str,
    case_key: str,
    version: int,
    payload: UnderwritingDecisionCreate,
) -> UnderwritingCaseDecision:
    record = get_case_version(session, workspace_id, case_key, version)
    decision = UnderwritingCaseDecision(
        workspace_id=workspace_id,
        case_version_id=record.id,
        decision=payload.decision,
        actor=payload.actor,
        rationale=payload.rationale,
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return decision


def latest_decision(session: Session, case_version_id: str) -> UnderwritingCaseDecision | None:
    return session.scalar(
        select(UnderwritingCaseDecision)
        .where(UnderwritingCaseDecision.case_version_id == case_version_id)
        .order_by(UnderwritingCaseDecision.created_at.desc(), UnderwritingCaseDecision.id.desc())
        .limit(1)
    )


def case_version_payload(session: Session, record: UnderwritingCaseVersion) -> dict:
    decision = latest_decision(session, record.id)
    return {
        "id": record.id,
        "workspace_id": record.workspace_id,
        "case_key": record.case_key,
        "label": record.label,
        "version": record.version,
        "parent_version_id": record.parent_version_id,
        "schema_version": record.schema_version,
        "assumptions": record.assumptions,
        "result": record.result,
        "approved_claim_ids": record.approved_claim_ids,
        "approved_claim_manifest": record.approved_claim_manifest,
        "claim_manifest_hash": record.claim_manifest_hash,
        "input_hash": record.input_hash,
        "output_hash": record.output_hash,
        "created_by": record.created_by,
        "change_note": record.change_note,
        "created_at": record.created_at,
        "latest_decision": decision,
    }
