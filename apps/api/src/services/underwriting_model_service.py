"""Deterministic private-equity underwriting calculations and version persistence."""

from __future__ import annotations

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
    DriverDistribution,
    MonteCarloRequest,
    MonteCarloResult,
    OperatingPeriodAssumption,
    ReturnsAttributionRequest,
    ReturnsAttributionResult,
    ReverseStressRequest,
    ReverseStressResult,
    SensitivityRequest,
    SensitivityResult,
    UnderwritingAssumptions,
    UnderwritingCaseCreate,
    UnderwritingDecisionCreate,
    UnderwritingResult,
    ValuationTriangulationRequest,
    ValuationTriangulationResult,
    WorkingCapitalPegRequest,
    WorkingCapitalPegResult,
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
    return max(0.0, cash), total_draw, total_paid, total_sweep, shortfall


def _covenant_results(
    assumptions: UnderwritingAssumptions,
    period_label: str,
    annualized_ebitda: float,
    capex: float,
    cash_taxes: float,
    cash_interest: float,
    paid_amortization: float,
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
    fixed_charges = cash_interest + paid_amortization
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


def calculate_projection(assumptions: UnderwritingAssumptions) -> list[dict]:
    periods = _projection_periods(assumptions)
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
        cash_before_debt = beginning_cash + ebitda - cash_taxes - capex - change_nwc - cash_interest
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
            paid_amort / year_fraction,
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
    pv_terminal = terminal_value / (1.0 + discount_rate) ** elapsed
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
    trailing = [row for row in eligible if row.observation_date >= cutoff] or eligible

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


def _sample_driver(rng: random.Random, distribution: DriverDistribution) -> float:
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
    invalid, or whose IRR/MoIC do not converge, are skipped and counted as ``failed``.
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
