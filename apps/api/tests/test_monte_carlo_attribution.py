"""Offline tests for Monte Carlo LBO simulation (G21) and the returns attribution bridge (G22)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.schemas.underwriting_model import (
    MonteCarloRequest,
    ReturnsAttributionRequest,
    UnderwritingAssumptions,
)
from src.services import underwriting_model_service as service

# The root integration registers this router in main.py. Keep focused tests independently runnable.
from src.main import app  # noqa: E402
from src.routers import underwriting_model as _underwriting_router  # noqa: E402

if not any("/underwriting/calculate" in getattr(route, "path", "") for route in app.routes):
    app.include_router(_underwriting_router.router)


def sample_assumptions(*, exit_multiple: float = 10.0) -> UnderwritingAssumptions:
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
                    "annual_revenue_growth": 0.08,
                    "gross_margin": 0.60,
                    "ebitda_margin": 0.20,
                    "da_percent_revenue": 0.03,
                    "capex_percent_revenue": 0.04,
                    "net_working_capital_percent_revenue": 0.10,
                    "cash_tax_rate": 0.25,
                    "base_rate": 0.04,
                },
                "periods": [{"label": f"Y{year}", "months": 12} for year in range(1, 6)],
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


def monte_carlo_payload(*, seed: int = 42, iterations: int = 100) -> dict:
    return {
        "assumptions": sample_assumptions().model_dump(mode="json"),
        "iterations": iterations,
        "seed": seed,
        "distributions": [
            {"driver": "exit_multiple", "kind": "normal", "mean": 10.0, "std_dev": 1.0},
            {"driver": "revenue_growth_shift", "kind": "uniform", "low": -0.02, "high": 0.02},
            {
                "driver": "ebitda_margin_shift",
                "kind": "triangular",
                "low": -0.02,
                "mode": 0.0,
                "high": 0.02,
            },
        ],
    }


@pytest.fixture(scope="module")
def workspace_id(client) -> str:
    response = client.post(
        "/api/workspaces", json={"name": "Monte Carlo Lab", "deal_type": "buyout"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_same_seed_is_byte_identical_and_different_seed_moves_the_median(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/monte-carlo"
    first = client.post(url, json=monte_carlo_payload(seed=42))
    second = client.post(url, json=monte_carlo_payload(seed=42))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.content == second.content

    other_seed = client.post(url, json=monte_carlo_payload(seed=7))
    assert other_seed.status_code == 200, other_seed.text
    assert other_seed.json()["irr"]["p50"] != first.json()["irr"]["p50"]


def test_monte_carlo_percentiles_are_ordered_and_iterations_are_accounted_for(
    client, workspace_id
):
    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/monte-carlo",
        json=monte_carlo_payload(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["iterations"] == 100
    assert body["seed"] == 42
    assert body["converged"] + body["failed"] == body["iterations"]
    assert body["converged"] > 0
    for metric in ("irr", "moic"):
        band = body[metric]
        assert band["p5"] <= band["p25"] <= band["p50"] <= band["p75"] <= band["p95"]
        assert band["p5"] <= band["mean"] <= band["p95"]
    assert 0.0 <= body["probability_irr_below_zero"] <= 1.0
    assert 0.0 <= body["probability_moic_below_1"] <= 1.0
    assert [summary["driver"] for summary in body["driver_summaries"]] == [
        "exit_multiple",
        "revenue_growth_shift",
        "ebitda_margin_shift",
    ]
    for summary in body["driver_summaries"]:
        assert summary["sampled_min"] <= summary["sampled_mean"] <= summary["sampled_max"]


def test_zero_variance_distributions_collapse_to_the_deterministic_result():
    assumptions = sample_assumptions()
    deterministic = service.run_underwriting(assumptions)
    result = service.run_monte_carlo(
        MonteCarloRequest.model_validate(
            {
                "assumptions": assumptions.model_dump(mode="json"),
                "iterations": 100,
                "seed": 42,
                "distributions": [
                    {"driver": "exit_multiple", "kind": "normal", "mean": 10.0, "std_dev": 0.0},
                    {
                        "driver": "revenue_growth_shift",
                        "kind": "uniform",
                        "low": 0.0,
                        "high": 0.0,
                    },
                ],
            }
        )
    )
    assert result.converged == 100
    assert result.failed == 0
    for band, expected in ((result.irr, deterministic.returns.xirr),
                           (result.moic, deterministic.returns.moic)):
        assert band.p5 == band.p25 == band.p50 == band.p75 == band.p95 == expected
        assert band.mean == pytest.approx(expected, abs=1e-9)


def test_equity_wipeouts_enter_the_sample_as_total_losses_not_failures():
    """Regression: a draw that wipes out sponsor equity has no positive cash flow, so no IRR
    solves — but the outcome is a total loss, not an invalid iteration. Censoring wipeouts into
    ``failed`` silently understated P(MoIC<1) and the loss percentiles exactly under stress.
    A wipeout must converge with its (negative) MoIC and an IRR of -100%."""
    assumptions = sample_assumptions()
    result = service.run_monte_carlo(
        MonteCarloRequest.model_validate(
            {
                "assumptions": assumptions.model_dump(mode="json"),
                "iterations": 100,
                "seed": 42,
                "distributions": [
                    # Exit at 0.5x EBITDA — exit equity is deeply negative in every iteration.
                    {"driver": "exit_multiple", "kind": "normal", "mean": 0.5, "std_dev": 0.0},
                ],
            }
        )
    )
    # Sanity-check the fixture really is a wipeout, not merely a low-multiple exit.
    stressed = assumptions.model_copy(deep=True)
    stressed.transaction.exit_multiple = 0.5
    deterministic = service.run_underwriting(stressed)
    assert deterministic.returns.sponsor_exit_proceeds <= 0
    assert deterministic.returns.xirr is None

    assert result.converged == 100
    assert result.failed == 0
    assert result.irr.p5 == result.irr.p50 == result.irr.p95 == -1.0
    assert result.moic.p50 == deterministic.returns.moic
    assert result.moic.p50 < 1.0
    assert result.probability_irr_below_zero == 1.0
    assert result.probability_moic_below_1 == 1.0


@pytest.mark.parametrize("exit_multiple", [10.0, 8.0])
def test_attribution_components_sum_exactly_to_total_value_creation(exit_multiple):
    result = service.calculate_returns_attribution(
        ReturnsAttributionRequest(assumptions=sample_assumptions(exit_multiple=exit_multiple))
    )
    total = Decimal(str(result.total_value_creation))
    component_sum = sum(Decimal(str(component.amount)) for component in result.components)
    assert component_sum == total
    assert result.reconciles is True
    assert Decimal(str(result.exit_equity)) - Decimal(str(result.entry_equity)) == total
    assert [component.key for component in result.components] == [
        "ebitda_growth",
        "multiple_change",
        "deleveraging",
        "cross_term",
    ]
    by_key = {component.key: component for component in result.components}
    assert by_key["ebitda_growth"].amount > 0  # EBITDA compounds at 8% revenue growth
    assert by_key["deleveraging"].amount > 0  # full cash sweep pays debt down
    if exit_multiple < 10.0:
        assert by_key["multiple_change"].amount < 0
        assert by_key["cross_term"].amount < 0
    else:
        assert by_key["multiple_change"].amount == 0.0
        assert by_key["cross_term"].amount == 0.0
    if total != 0:
        share_sum = sum(
            Decimal(str(component.share_of_total)) for component in result.components
        )
        assert share_sum == pytest.approx(Decimal("1"), abs=Decimal("1e-6"))


def test_attribution_endpoint_reconciles(client, workspace_id):
    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/returns-attribution",
        json={"assumptions": sample_assumptions(exit_multiple=8.0).model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reconciles"] is True
    assert sum(Decimal(str(component["amount"])) for component in body["components"]) == Decimal(
        str(body["total_value_creation"])
    )


def test_monte_carlo_validation_rejects_bad_iterations_and_unknown_drivers(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/monte-carlo"

    too_few = monte_carlo_payload()
    too_few["iterations"] = 99
    assert client.post(url, json=too_few).status_code == 422

    too_many = monte_carlo_payload()
    too_many["iterations"] = 5_001
    assert client.post(url, json=too_many).status_code == 422

    unknown_driver = monte_carlo_payload()
    unknown_driver["distributions"][0]["driver"] = "entry_leverage_turns"
    assert client.post(url, json=unknown_driver).status_code == 422

    missing_params = monte_carlo_payload()
    missing_params["distributions"][0] = {"driver": "exit_multiple", "kind": "normal"}
    assert client.post(url, json=missing_params).status_code == 422
