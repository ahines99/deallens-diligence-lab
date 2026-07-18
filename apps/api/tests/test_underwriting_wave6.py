"""Offline tests for Wave 6 Theme K underwriting depth: the one-way sensitivity tornado (G69),
the dividend-recap solver (G70), working-capital facility sizing (G71), fund-level Monte Carlo
with shared macro factors (G72), and the year-by-year value-creation waterfall (G73).

Every quantitative claim is pinned to a hand-computed fixture, not just a shape check."""

from __future__ import annotations

import copy
import uuid
from datetime import date
from decimal import Decimal

import pytest

from src.db.session import SessionLocal
from src.models.deal_workflow import Deal, Fund, Organization
from src.models.underwriting_model import UnderwritingCaseVersion
from src.models.workspace import Workspace
from src.schemas.underwriting_model import (
    DividendRecapSolveRequest,
    FacilitySizingRequest,
    FundMonteCarloRequest,
    ReturnsAttributionRequest,
    SensitivityTornadoRequest,
    UnderwritingAssumptions,
)
from src.services import underwriting_model_service as service

# The root integration registers this router in main.py. Keep focused tests independently runnable.
from src.main import app  # noqa: E402
from src.routers import underwriting_model as _underwriting_router  # noqa: E402

if not any("/underwriting/calculate" in getattr(route, "path", "") for route in app.routes):
    app.include_router(_underwriting_router.router)


def sample_assumptions(
    *,
    exit_multiple: float = 10.0,
    annual_revenue_growth: float = 0.08,
    quarterly: bool = False,
) -> UnderwritingAssumptions:
    if quarterly:
        periods = [{"label": f"Q{quarter:02d}", "months": 3} for quarter in range(1, 21)]
    else:
        periods = [{"label": f"Y{year}", "months": 12} for year in range(1, 6)]
    return UnderwritingAssumptions.model_validate(
        {
            "historical": {
                "ltm_revenue": 1_000.0,
                "ltm_ebitda": 200.0,
                "starting_cash": 50.0,
                "starting_net_working_capital": 100.0,
                "existing_debt": 100.0,
            },
            "transaction": {
                "close_date": "2026-01-01",
                "entry_multiple": 10.0,
                "exit_multiple": exit_multiple,
                "hold_period_years": 5.0,
                "transaction_fees": 50.0,
                "seller_rollover": 100.0,
                "minimum_cash": 25.0,
                "cash_sweep_percent": 1.0,
            },
            "projection": {
                "default_drivers": {
                    "annual_revenue_growth": annual_revenue_growth,
                    "gross_margin": 0.60,
                    "ebitda_margin": 0.20,
                    "da_percent_revenue": 0.03,
                    "capex_percent_revenue": 0.04,
                    "net_working_capital_percent_revenue": 0.10,
                    "cash_tax_rate": 0.25,
                    "base_rate": 0.04,
                },
                "periods": periods,
            },
            "debt_tranches": [
                {
                    "name": "Revolver",
                    "tranche_type": "revolver",
                    "initial_amount": 0.0,
                    "commitment": 150.0,
                    "spread": 0.03,
                    "cash_sweep_priority": 0,
                },
                {
                    "name": "First Lien",
                    "tranche_type": "term_loan",
                    "initial_amount": 800.0,
                    "senior": True,
                    "spread": 0.04,
                    "base_rate_floor": 0.05,
                    "annual_amortization_rate": 0.02,
                    "cash_sweep_priority": 10,
                    "oid_discount": 0.02,
                    "financing_fee_percent": 0.01,
                },
                {
                    "name": "Mezzanine",
                    "tranche_type": "mezzanine",
                    "initial_amount": 200.0,
                    "senior": False,
                    "spread": 0.08,
                    "pik_rate": 0.04,
                    "cash_sweep_priority": 20,
                },
            ],
            "valuation": {
                "discount_rate": 0.10,
                "terminal_growth_rate": 0.025,
                "mid_year_convention": True,
            },
        }
    )


def recap_assumptions() -> UnderwritingAssumptions:
    """A fully hand-solvable 3-year deal: flat 200 EBITDA, zero taxes/capex/NWC, no sweep.

    Per year: interest = 400 x 8% = 32, so cash builds by exactly 168/yr from a 0 open
    (Y1 168, Y2 336, Y3 504); liquidity = cash + 200 undrawn revolver (368/536/704);
    leverage = 400/200 = 2.0x flat.
    """
    return UnderwritingAssumptions.model_validate(
        {
            "historical": {
                "ltm_revenue": 1_000.0,
                "ltm_ebitda": 200.0,
                "starting_cash": 0.0,
                "starting_net_working_capital": 0.0,
                "existing_debt": 0.0,
            },
            "transaction": {
                "close_date": "2026-01-01",
                "entry_multiple": 8.0,
                "exit_multiple": 8.0,
                "hold_period_years": 3.0,
                "minimum_cash": 0.0,
                "cash_sweep_percent": 0.0,
            },
            "projection": {
                "default_drivers": {
                    "annual_revenue_growth": 0.0,
                    "gross_margin": 0.60,
                    "ebitda_margin": 0.20,
                    "da_percent_revenue": 0.0,
                    "capex_percent_revenue": 0.0,
                    "net_working_capital_percent_revenue": 0.0,
                    "cash_tax_rate": 0.0,
                    "base_rate": 0.04,
                },
                "periods": [{"label": f"Y{year}", "months": 12} for year in range(1, 4)],
            },
            "debt_tranches": [
                {
                    "name": "Revolver",
                    "tranche_type": "revolver",
                    "initial_amount": 0.0,
                    "commitment": 200.0,
                    "spread": 0.02,
                    "cash_sweep_priority": 0,
                },
                {
                    "name": "Term Loan",
                    "tranche_type": "term_loan",
                    "initial_amount": 400.0,
                    "spread": 0.04,
                    "cash_sweep_priority": 10,
                },
            ],
        }
    )


def seasonal_months() -> list[dict]:
    # The G25 fixture shape: trough in month 1 (80), peak in month 8 (200), average 136.25.
    levels = [80, 90, 100, 120, 150, 170, 190, 200, 180, 150, 110, 95]
    return [{"month": index + 1, "value": value} for index, value in enumerate(levels)]


@pytest.fixture(scope="module")
def workspace_id(client) -> str:
    response = client.post(
        "/api/workspaces", json={"name": "Underwriting Wave6 Lab", "deal_type": "buyout"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- G69 One-way sensitivity tornado ----------------------------------------------------------


def test_tornado_deltas_match_direct_apply_variable_runs():
    assumptions = sample_assumptions()
    result = service.calculate_sensitivity_tornado(
        SensitivityTornadoRequest(assumptions=assumptions, metric="moic")
    )
    base = service.run_underwriting(assumptions).returns.moic
    assert result.base_metric == base

    row = next(row for row in result.rows if row.variable == "exit_multiple")
    # Relative convention: 10x base shifted +/-10% -> 9x / 11x.
    assert row.convention == "relative"
    assert row.base_value == 10.0 and row.low_value == 9.0 and row.high_value == 11.0
    low_direct = service.run_underwriting(
        service._apply_variable(assumptions, "exit_multiple", 9.0)
    ).returns.moic
    high_direct = service.run_underwriting(
        service._apply_variable(assumptions, "exit_multiple", 11.0)
    ).returns.moic
    assert row.metric_low == low_direct
    assert row.metric_high == high_direct
    assert row.delta_low == pytest.approx(low_direct - base, abs=1e-8)
    assert row.delta_high == pytest.approx(high_direct - base, abs=1e-8)
    assert row.max_abs_delta == pytest.approx(
        max(abs(row.delta_low), abs(row.delta_high)), abs=1e-8
    )

    shift_row = next(row for row in result.rows if row.variable == "revenue_growth_shift")
    # Absolute convention: additive shifts around a base of zero (+/-100 bps by default).
    assert shift_row.convention == "absolute"
    assert shift_row.base_value == 0.0
    assert shift_row.low_value == -0.01 and shift_row.high_value == 0.01


def test_tornado_ranks_every_variable_by_max_abs_delta():
    result = service.calculate_sensitivity_tornado(
        SensitivityTornadoRequest(assumptions=sample_assumptions(), metric="moic")
    )
    assert len(result.rows) == 5
    assert all(row.evaluable for row in result.rows)
    deltas = [row.max_abs_delta for row in result.rows]
    assert deltas == sorted(deltas, reverse=True)
    # On this fixture the entry multiple dominates (it moves both price and equity check).
    assert result.rows[0].variable == "entry_multiple"


def test_tornado_inevaluable_extremes_are_reported_never_dropped():
    # relative_shift 0.9 -> entry multiple low = 1x: debt + rollover overfund the deal.
    # absolute_shift 0.45 -> EBITDA margin high = 65% > 60% gross margin: invalid assumptions.
    result = service.calculate_sensitivity_tornado(
        SensitivityTornadoRequest(
            assumptions=sample_assumptions(),
            metric="moic",
            relative_shift=0.9,
            absolute_shift=0.45,
        )
    )
    assert len(result.rows) == 5  # nothing is dropped
    by_variable = {row.variable: row for row in result.rows}

    entry = by_variable["entry_multiple"]
    assert entry.evaluable is False
    assert entry.reason is not None and "overfunded" in entry.reason
    assert entry.metric_low is None and entry.max_abs_delta is None

    margin = by_variable["ebitda_margin_shift"]
    assert margin.evaluable is False
    assert margin.reason is not None and "invalid" in margin.reason
    assert margin.metric_low is not None  # the surviving extreme is still reported

    # Inevaluable rows always sort after every evaluable row.
    flags = [row.evaluable for row in result.rows]
    assert flags == sorted(flags, reverse=True)


def test_tornado_endpoint_contract_and_validation(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/sensitivity-tornado"
    payload = {"assumptions": sample_assumptions().model_dump(mode="json")}
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metric"] == "irr"
    assert len(body["rows"]) == 5

    subset = client.post(
        url, json={**payload, "variables": ["exit_multiple", "revenue_growth_shift"]}
    )
    assert subset.status_code == 200
    assert {row["variable"] for row in subset.json()["rows"]} == {
        "exit_multiple",
        "revenue_growth_shift",
    }

    duplicate = client.post(url, json={**payload, "variables": ["exit_multiple", "exit_multiple"]})
    assert duplicate.status_code == 422

    bad = copy.deepcopy(payload)
    bad["assumptions"]["historical"]["ltm_revenue"] = -5.0
    assert client.post(url, json=bad).status_code == 422


# --- G70 Dividend recap solver ----------------------------------------------------------------


def test_special_distribution_seam_flows_through_the_cash_waterfall():
    assumptions = recap_assumptions()
    projection = service.calculate_projection(
        assumptions, special_distributions={"Y2": 100.0}
    )
    rows = {row["label"]: row for row in projection}
    assert rows["Y1"]["special_distribution"] == 0.0
    assert rows["Y2"]["special_distribution"] == 100.0
    # Hand math: Y2 cash 336 - 100 = 236; the deficit carries so Y3 = 504 - 100 = 404.
    assert rows["Y1"]["ending_cash"] == 168.0
    assert rows["Y2"]["ending_cash"] == 236.0
    assert rows["Y3"]["ending_cash"] == 404.0
    assert rows["Y2"]["liquidity"] == 436.0  # 236 cash + 200 undrawn revolver

    with pytest.raises(service.UnderwritingCalculationError, match="absent"):
        service.calculate_projection(assumptions, special_distributions={"Y9": 10.0})
    with pytest.raises(service.UnderwritingCalculationError, match="negative"):
        service.calculate_projection(assumptions, special_distributions={"Y2": -1.0})


def test_recap_solver_matches_hand_solved_liquidity_bound():
    # Liquidity from Y2 on is (536 - D) then (704 - D): min_liquidity 300 -> D_max = 236.
    result = service.solve_dividend_recap(
        DividendRecapSolveRequest(
            assumptions=recap_assumptions(),
            period="Y2",
            min_liquidity=300.0,
            max_total_leverage=4.0,
        )
    )
    assert result.status == "solved"
    assert result.max_distribution == pytest.approx(236.0, abs=0.02)
    assert result.binding_constraint == "min_liquidity"
    # No rollover -> the sponsor owns 100% of the distribution.
    assert result.sponsor_share == result.max_distribution

    by_name = {item.name: item for item in result.constraints}
    liquidity = by_name["min_liquidity"]
    assert liquidity.satisfied is True
    assert liquidity.binding_period == "Y2"
    assert liquidity.actual == pytest.approx(300.0, abs=0.03)
    # Leverage never moves (the distribution is cash-funded below the 336 cash balance).
    assert by_name["max_total_leverage"].actual == pytest.approx(2.0, abs=1e-6)


def test_recap_solver_names_the_leverage_constraint_when_it_binds():
    # Beyond 336 the revolver funds the distribution: debt 400 + draw. Leverage <= 2.5x
    # caps the draw at 100 -> D_max = 336 + 100 = 436.
    result = service.solve_dividend_recap(
        DividendRecapSolveRequest(
            assumptions=recap_assumptions(),
            period="Y2",
            min_liquidity=1.0,
            max_total_leverage=2.5,
        )
    )
    assert result.status == "solved"
    assert result.max_distribution == pytest.approx(436.0, abs=0.02)
    assert result.binding_constraint == "max_total_leverage"
    leverage = next(item for item in result.constraints if item.name == "max_total_leverage")
    assert leverage.actual == pytest.approx(2.5, abs=1e-3)


def test_recap_solver_reports_infeasible_with_the_violated_constraint_at_zero():
    result = service.solve_dividend_recap(
        DividendRecapSolveRequest(
            assumptions=recap_assumptions(), period="Y2", min_liquidity=600.0
        )
    )
    assert result.status == "infeasible"
    assert result.binding_constraint == "min_liquidity"
    assert result.max_distribution is None and result.sponsor_share is None
    assert result.note is not None and "no distribution" in result.note
    liquidity = next(item for item in result.constraints if item.name == "min_liquidity")
    # Constraints are tested from the distribution period ON: worst liquidity is Y2's 536,
    # never Y1's 368 (an earlier period the distribution cannot affect).
    assert liquidity.actual == 536.0
    assert liquidity.binding_period == "Y2"
    assert liquidity.satisfied is False


def test_recap_solver_reports_unbounded_honestly():
    # Leverage-only constraint: once the revolver is exhausted leverage plateaus at
    # 600/200 = 3x < 10x, so no constraint ever binds — named, not a fabricated number.
    result = service.solve_dividend_recap(
        DividendRecapSolveRequest(
            assumptions=recap_assumptions(), period="Y2", max_total_leverage=10.0
        )
    )
    assert result.status == "unbounded"
    assert result.max_distribution is None
    assert result.note is not None and "unbounded" in result.note


def test_recap_solver_endpoint_and_unknown_period_is_422(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/dividend-recap-solve"
    payload = {
        "assumptions": recap_assumptions().model_dump(mode="json"),
        "period": "Y2",
        "min_liquidity": 300.0,
    }
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "solved"

    assert client.post(url, json={**payload, "period": "Y9"}).status_code == 422
    no_constraints = {
        "assumptions": recap_assumptions().model_dump(mode="json"),
        "period": "Y2",
    }
    assert client.post(url, json=no_constraints).status_code == 422


# --- G71 Working-capital facility sizing ------------------------------------------------------


def test_facility_sizing_peak_draw_matches_hand_computed_seasonal_scaling():
    result = service.calculate_facility_sizing(
        FacilitySizingRequest(
            assumptions=sample_assumptions(),
            monthly_working_capital=seasonal_months(),
        )
    )
    assert result.status == "complete"
    assert result.seasonal_annual_average == 136.25
    assert result.seasonal_peak_month == 8
    assert result.commitment == 150.0  # the modeled revolver commitment
    assert result.commitment_source == "modeled_revolvers"
    assert len(result.years) == 5

    # Hand math for Y1: annual NWC = 10% x (1000 x 1.08) = 108.0. Peak month scales the
    # profile proportionally: 200 x 108 / 136.25 = 158.53, so the peak draw above the
    # annually funded level is 50.53 and headroom is 150 - 0 - 50.53 = 99.47.
    year_one = result.years[0]
    assert year_one.evaluable is True
    assert year_one.annual_nwc == 108.0
    assert year_one.peak_month == 8
    assert year_one.peak_monthly_nwc == 158.53
    assert year_one.peak_draw == 50.53
    assert year_one.existing_revolver_draw == 0.0
    assert year_one.headroom == 99.47

    # Every later year applies the same documented formula to its own annual level.
    projection = service.calculate_projection(sample_assumptions())
    for year, row in zip(result.years, projection):
        assert year.annual_nwc == row["net_working_capital"]
        expected_peak = round(200.0 * year.annual_nwc / 136.25, 2)
        assert year.peak_monthly_nwc == expected_peak
        assert year.peak_draw == round(
            200.0 * year.annual_nwc / 136.25 - year.annual_nwc, 2
        )
    # Working capital grows with revenue, so the largest need is the final year.
    assert result.peak_year_label == "Y5"
    assert result.peak_draw == result.years[-1].peak_draw


def test_facility_sizing_headroom_goes_negative_when_undersized():
    result = service.calculate_facility_sizing(
        FacilitySizingRequest(
            assumptions=sample_assumptions(),
            monthly_working_capital=seasonal_months(),
            commitment_override=20.0,
        )
    )
    assert result.commitment == 20.0
    assert result.commitment_source == "override"
    year_one = result.years[0]
    # 20 - 0 - 50.53: negative headroom = the facility is undersized, sign preserved.
    assert year_one.headroom == -30.53
    assert all(year.headroom < 0 for year in result.years if year.evaluable)


def test_facility_sizing_without_seasonality_is_unavailable_never_a_flat_profile(
    client, workspace_id
):
    result = service.calculate_facility_sizing(
        FacilitySizingRequest(assumptions=sample_assumptions())
    )
    assert result.status == "unavailable"
    assert result.reason is not None and "fabricated" in result.reason
    assert result.years == []
    assert result.peak_draw is None and result.peak_year_label is None

    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/facility-sizing",
        json={"assumptions": sample_assumptions().model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unavailable"


def test_facility_sizing_partial_seasonality_carries_missing_months():
    result = service.calculate_facility_sizing(
        FacilitySizingRequest(
            assumptions=sample_assumptions(),
            monthly_working_capital=[
                {"month": 1, "value": 100.0},
                {"month": 6, "value": 200.0},
            ],
        )
    )
    assert result.status == "partial"
    assert result.seasonality_missing_months == [2, 3, 4, 5, 7, 8, 9, 10, 11, 12]
    assert result.seasonal_peak_month == 6
    # The swing is measured over PRESENT months only (average 150): 200/150 scaling.
    year_one = result.years[0]
    assert year_one.peak_monthly_nwc == round(200.0 * 108.0 / 150.0, 2)


def test_facility_sizing_endpoint_contract(client, workspace_id):
    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/facility-sizing",
        json={
            "assumptions": sample_assumptions().model_dump(mode="json"),
            "monthly_working_capital": seasonal_months(),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"
    assert body["years"][0]["peak_draw"] == 50.53


# --- G72 Fund-level Monte Carlo ---------------------------------------------------------------


def fund_mc_payload(*, seed: int = 42, iterations: int = 100) -> dict:
    """Two deals with idiosyncratic growth draws plus one shared exit-multiple factor."""
    return {
        "deals": [
            {
                "name": "Alpha",
                "assumptions": sample_assumptions().model_dump(mode="json"),
                "distributions": [
                    {
                        "driver": "revenue_growth_shift",
                        "kind": "uniform",
                        "low": -0.02,
                        "high": 0.02,
                    }
                ],
            },
            {
                "name": "Beta",
                "assumptions": sample_assumptions(exit_multiple=8.0).model_dump(mode="json"),
                "distributions": [
                    {
                        "driver": "revenue_growth_shift",
                        "kind": "uniform",
                        "low": -0.02,
                        "high": 0.02,
                    }
                ],
            },
        ],
        "iterations": iterations,
        "seed": seed,
        "factors": [{"name": "multiple_shift", "kind": "normal", "mean": 0.0, "std_dev": 1.0}],
    }


def test_fund_mc_same_seed_is_byte_identical_and_different_seed_moves_the_band(
    client, workspace_id
):
    url = f"/api/workspaces/{workspace_id}/underwriting/fund-monte-carlo"
    first = client.post(url, json=fund_mc_payload(seed=42))
    second = client.post(url, json=fund_mc_payload(seed=42))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.content == second.content

    other = client.post(url, json=fund_mc_payload(seed=7))
    assert other.status_code == 200, other.text
    assert other.json()["fund_irr"]["p50"] != first.json()["fund_irr"]["p50"]


def test_fund_mc_correlated_bands_widen_versus_the_zero_loadings_rerun():
    """The acceptance core: the SAME seed re-run with loadings zeroed must show narrower
    bands — correlated p5 <= independent p5 (and p95 >=) with positive loadings."""
    result = service.run_fund_monte_carlo(FundMonteCarloRequest.model_validate(fund_mc_payload()))
    effect = result.correlation_effect

    assert result.fund_moic.p5 <= effect.independent_moic.p5
    assert result.fund_moic.p95 >= effect.independent_moic.p95
    assert result.fund_irr.p5 <= effect.independent_irr.p5
    assert result.fund_irr.p95 >= effect.independent_irr.p95
    # On this fixture the shared N(0,1) exit-multiple factor dominates: strict widening.
    assert result.fund_moic.p5 < effect.independent_moic.p5
    assert result.fund_moic.p95 > effect.independent_moic.p95

    assert effect.moic_p5_spread == round(result.fund_moic.p5 - effect.independent_moic.p5, 8)
    assert effect.moic_p5_spread < 0 and effect.moic_p95_spread > 0
    assert effect.irr_p5_spread < 0 and effect.irr_p95_spread > 0
    # Both runs consumed the identical draw plan.
    assert result.converged + result.failed == result.iterations
    assert effect.independent_converged + effect.independent_failed == result.iterations


def test_fund_mc_commitment_weighted_moic_and_pooled_irr_reconcile_by_hand():
    """Degenerate factor (std 0) -> every iteration is the deterministic case, so the fund
    metrics must equal the hand-built commitment-weighted MoIC and pooled scaled XIRR."""
    alpha = sample_assumptions()
    beta = sample_assumptions(exit_multiple=8.0)
    alpha_returns = service.run_underwriting(alpha).returns
    beta_returns = service.run_underwriting(beta).returns

    result = service.run_fund_monte_carlo(
        FundMonteCarloRequest.model_validate(
            {
                "deals": [
                    {
                        "name": "Alpha",
                        "assumptions": alpha.model_dump(mode="json"),
                        "commitment": 100.0,
                    },
                    {
                        "name": "Beta",
                        "assumptions": beta.model_dump(mode="json"),
                        "commitment": 300.0,
                    },
                ],
                "iterations": 100,
                "seed": 42,
                "factors": [
                    {"name": "rate_shift", "kind": "normal", "mean": 0.0, "std_dev": 0.0}
                ],
            }
        )
    )
    assert result.converged == 100 and result.failed == 0
    assert result.total_commitment == 400.0

    expected_moic = round(
        (100.0 * alpha_returns.moic + 300.0 * beta_returns.moic) / 400.0, 8
    )
    band = result.fund_moic
    assert band.p5 == band.p50 == band.p95 == expected_moic

    # Pooled IRR: each deal's dated sponsor flows scaled to its commitment.
    close = date(2026, 1, 1)
    end = date(2030, 12, 31)
    scale_a = 100.0 / alpha_returns.sponsor_invested_capital
    scale_b = 300.0 / beta_returns.sponsor_invested_capital
    expected_irr = service.xirr(
        [
            (close, -alpha_returns.sponsor_invested_capital * scale_a),
            (end, alpha_returns.sponsor_exit_proceeds * scale_a),
            (close, -beta_returns.sponsor_invested_capital * scale_b),
            (end, beta_returns.sponsor_exit_proceeds * scale_b),
        ]
    )
    assert result.fund_irr.p50 == pytest.approx(expected_irr, abs=1e-8)

    # Per-deal marginal bands collapse to each deal's deterministic outcome.
    by_name = {deal.name: deal for deal in result.deals}
    assert by_name["Alpha"].moic.p50 == alpha_returns.moic
    assert by_name["Beta"].moic.p50 == beta_returns.moic
    assert by_name["Alpha"].commitment == 100.0
    assert by_name["Alpha"].base_invested == alpha_returns.sponsor_invested_capital


def test_fund_mc_wipeouts_enter_as_total_losses_not_failures():
    # A -9.5 turn multiple shift takes both deals to a 0.5x exit: every iteration wipes out.
    result = service.run_fund_monte_carlo(
        FundMonteCarloRequest.model_validate(
            {
                "deals": [
                    {"name": "Alpha", "assumptions": sample_assumptions().model_dump(mode="json")},
                    {"name": "Beta", "assumptions": sample_assumptions().model_dump(mode="json")},
                ],
                "iterations": 100,
                "seed": 42,
                "factors": [
                    {"name": "multiple_shift", "kind": "normal", "mean": -9.5, "std_dev": 0.0}
                ],
            }
        )
    )
    assert result.converged == 100 and result.failed == 0
    assert result.fund_irr.p5 == result.fund_irr.p50 == result.fund_irr.p95 == -1.0
    assert result.probability_fund_moic_below_1 == 1.0
    for deal in result.deals:
        assert deal.irr.p50 == -1.0
        assert deal.probability_moic_below_1 == 1.0


def test_fund_mc_resolves_a_saved_fund_construction_with_exclusion_honesty(
    client, workspace_id
):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as session:
        organization = Organization(name=f"FundMC {suffix}", slug=f"fund-mc-{suffix}")
        session.add(organization)
        session.flush()
        fund = Fund(
            organization_id=organization.id,
            name="Wave6 Fund I",
            vintage_year=2024,
            strategy="buyout",
        )
        session.add(fund)
        session.flush()
        fund_id = fund.id

        def add_deal(code: str, *, committed: float | None, valid_assumptions: bool) -> None:
            deal_workspace = Workspace(
                name=f"{code} Underwrite",
                organization_id=organization.id,
                deal_type="buyout",
                investment_question=f"Acquire {code}?",
            )
            session.add(deal_workspace)
            session.flush()
            session.add(
                Deal(
                    organization_id=organization.id,
                    fund_id=fund.id,
                    workspace_id=deal_workspace.id,
                    code=code,
                    name=f"Project {code}",
                    target_company=f"{code} Co",
                    stage="diligence",
                    status="active",
                )
            )
            if committed is not None:
                assumptions = (
                    sample_assumptions().model_dump(mode="json") if valid_assumptions else {}
                )
                session.add(
                    UnderwritingCaseVersion(
                        workspace_id=deal_workspace.id,
                        case_key="base",
                        label="Base",
                        version=1,
                        assumptions=assumptions,
                        result={"sources_uses": {"sponsor_equity": committed}},
                        input_hash=f"{uuid.uuid4().hex:0>64}",
                        output_hash=f"{uuid.uuid4().hex:0>64}",
                        created_by="associate@example.test",
                    )
                )

        add_deal("W6-A", committed=700.0, valid_assumptions=True)
        add_deal("W6-B", committed=999.0, valid_assumptions=True)
        add_deal("W6-C", committed=None, valid_assumptions=True)  # unsized: no case at all
        add_deal("W6-D", committed=500.0, valid_assumptions=False)  # sized but unusable
        session.commit()

    url = f"/api/workspaces/{workspace_id}/underwriting/fund-monte-carlo"
    response = client.post(
        url,
        json={
            "fund_id": fund_id,
            "iterations": 100,
            "seed": 42,
            "factors": [{"name": "rate_shift", "kind": "normal", "mean": 0.0, "std_dev": 0.0}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "fund_construction"
    assert body["fund_id"] == fund_id
    # Committed sponsor equity from the sizing case IS the commitment — never imputed.
    assert {(deal["name"], deal["commitment"]) for deal in body["deals"]} == {
        ("W6-A", 700.0),
        ("W6-B", 999.0),
    }
    assert body["total_commitment"] == 1699.0
    assert {(item["code"], item["reason"]) for item in body["excluded_deals"]} == {
        ("W6-C", "no underwriting case"),
        ("W6-D", "case assumptions are not a valid underwriting model"),
    }

    missing = client.post(
        url,
        json={
            "fund_id": "does-not-exist",
            "iterations": 100,
            "factors": [{"name": "rate_shift", "kind": "normal", "mean": 0.0, "std_dev": 0.0}],
        },
    )
    assert missing.status_code == 404


def test_fund_mc_validation_rejects_bad_iterations_and_ambiguous_sources(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/fund-monte-carlo"

    too_few = fund_mc_payload()
    too_few["iterations"] = 99
    assert client.post(url, json=too_few).status_code == 422

    too_many = fund_mc_payload()
    too_many["iterations"] = 2_001
    assert client.post(url, json=too_many).status_code == 422

    both_sources = fund_mc_payload()
    both_sources["fund_id"] = "some-fund"
    assert client.post(url, json=both_sources).status_code == 422

    neither = fund_mc_payload()
    neither["deals"] = []
    assert client.post(url, json=neither).status_code == 422

    duplicate_factor = fund_mc_payload()
    duplicate_factor["factors"] = [
        {"name": "rate_shift", "kind": "normal", "mean": 0.0, "std_dev": 0.01},
        {"name": "rate_shift", "kind": "normal", "mean": 0.0, "std_dev": 0.02},
    ]
    assert client.post(url, json=duplicate_factor).status_code == 422


# --- G73 Year-by-year value-creation waterfall ------------------------------------------------


@pytest.mark.parametrize("exit_multiple", [10.0, 8.0])
def test_annual_waterfall_reconciles_each_year_and_the_g22_total(exit_multiple):
    assumptions = sample_assumptions(exit_multiple=exit_multiple)
    result = service.calculate_annual_value_creation(assumptions)
    attribution = service.calculate_returns_attribution(
        ReturnsAttributionRequest(assumptions=assumptions)
    )
    assert len(result.years) == 5
    assert result.reconciles is True
    assert result.matches_attribution_total is True

    # Per-year legs sum EXACTLY to that year's equity-value change (Decimal identity).
    for year in result.years:
        legs = (
            Decimal(str(year.ebitda_growth))
            + Decimal(str(year.multiple_change))
            + Decimal(str(year.deleveraging))
            + Decimal(str(year.cross_term))
        )
        assert legs == Decimal(str(year.equity_change))
        assert year.reconciles is True

    # The years telescope EXACTLY to the G22 bridge total.
    year_sum = sum((Decimal(str(year.equity_change)) for year in result.years), Decimal("0"))
    assert year_sum == Decimal(str(result.total_value_creation))
    assert Decimal(str(result.total_value_creation)) == Decimal(
        str(attribution.total_value_creation)
    )
    assert result.entry_equity == attribution.entry_equity
    assert result.exit_equity == attribution.exit_equity

    # Multiple change is allocated to the FINAL year only, and it IS G22's leg
    # ((exit - entry) x entry EBITDA); interim years mark at the entry multiple.
    by_key = {component.key: component for component in attribution.components}
    for year in result.years[:-1]:
        assert year.multiple_change == 0.0
        assert year.applied_multiple == 10.0
    final = result.years[-1]
    assert final.applied_multiple == exit_multiple
    assert final.multiple_change == by_key["multiple_change"].amount
    # The final-year cross term carries the multiple x EBITDA interaction — G22's cross term.
    assert final.cross_term == by_key["cross_term"].amount

    # Column totals reconcile to the G22 components (entry multiple has one decimal, so
    # per-year cent rounding is exact on this fixture).
    assert result.totals["ebitda_growth"] == by_key["ebitda_growth"].amount
    assert result.totals["multiple_change"] == by_key["multiple_change"].amount
    assert result.totals["deleveraging"] == by_key["deleveraging"].amount
    assert result.totals["cross_term"] == by_key["cross_term"].amount


def test_annual_waterfall_year_one_legs_are_hand_computable():
    assumptions = sample_assumptions()
    result = service.calculate_annual_value_creation(assumptions)
    projection = service.calculate_projection(assumptions)
    year_one_row = projection[0]

    # Entry net debt: 0 + 800 + 200 new debt less the 25 funded minimum cash = 975.
    assert result.entry_net_debt == 975.0
    assert result.entry_equity == 10.0 * 200.0 - 975.0  # 1025.00

    year_one = result.years[0]
    ebitda_1 = Decimal(str(year_one_row["ebitda"]))
    net_debt_1 = Decimal(str(year_one_row["total_debt"])) - Decimal(
        str(year_one_row["ending_cash"])
    )
    assert Decimal(str(year_one.ebitda_growth)) == (
        Decimal("10.0") * (ebitda_1 - Decimal("200"))
    ).quantize(Decimal("0.01"))
    assert Decimal(str(year_one.deleveraging)) == (Decimal("975") - net_debt_1).quantize(
        Decimal("0.01")
    )
    # Constant entry multiple through interim years: no cross term on this fixture.
    assert year_one.cross_term == 0.0
    assert year_one.multiple_change == 0.0


def test_annual_waterfall_groups_quarterly_periods_into_hold_years():
    quarterly = sample_assumptions(quarterly=True)
    result = service.calculate_annual_value_creation(quarterly)
    attribution = service.calculate_returns_attribution(
        ReturnsAttributionRequest(assumptions=quarterly)
    )
    assert [year.year_label for year in result.years] == ["Y1", "Y2", "Y3", "Y4", "Y5"]
    assert [year.period_label for year in result.years] == [
        "Q04",
        "Q08",
        "Q12",
        "Q16",
        "Q20",
    ]
    assert all(year.months == 12 for year in result.years)
    assert result.reconciles is True
    assert Decimal(str(result.total_value_creation)) == Decimal(
        str(attribution.total_value_creation)
    )


def test_annual_waterfall_straddling_period_fails_closed(client, workspace_id):
    data = sample_assumptions().model_dump(mode="json")
    data["transaction"]["hold_period_years"] = 2.5
    data["projection"]["periods"] = [{"label": "P1", "months": 30}]
    data["debt_tranches"] = []
    straddling = UnderwritingAssumptions.model_validate(data)
    with pytest.raises(service.UnderwritingCalculationError, match="straddles"):
        service.calculate_annual_value_creation(straddling)

    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/annual-value-creation",
        json={"assumptions": straddling.model_dump(mode="json")},
    )
    assert response.status_code == 422


def test_annual_value_creation_endpoint(client, workspace_id):
    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/annual-value-creation",
        json={"assumptions": sample_assumptions(exit_multiple=8.0).model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reconciles"] is True
    assert body["matches_attribution_total"] is True
    assert len(body["years"]) == 5
    assert sum(Decimal(str(year["equity_change"])) for year in body["years"]) == Decimal(
        str(body["total_value_creation"])
    )
