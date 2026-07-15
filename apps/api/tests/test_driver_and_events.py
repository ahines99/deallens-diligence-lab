"""Offline tests for the driver-based operating model (G24), working-capital seasonality (G25),
and dividend-recap / bolt-on event modeling (G26)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.schemas.underwriting_model import (
    DriverModelRequest,
    RecapBoltOnRequest,
    UnderwritingAssumptions,
    WorkingCapitalSeasonalityRequest,
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


@pytest.fixture(scope="module")
def workspace_id(client) -> str:
    response = client.post(
        "/api/workspaces", json={"name": "Driver + Events Lab", "deal_type": "buyout"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- G24 Driver-based operating model ---------------------------------------------------------


def _drivers() -> list[dict]:
    return [
        {"name": "units", "formula": "100"},
        {"name": "price", "formula": "12.5"},
        {"name": "revenue", "formula": "units * price", "provenance": "board deck"},
        {"name": "margin", "formula": "0.20"},
        {"name": "ebitda", "formula": "revenue * margin"},
    ]


def test_driver_model_resolves_dag_in_topological_order_with_provenance():
    result = service.calculate_driver_model(DriverModelRequest.model_validate({"drivers": _drivers()}))
    values = {row.name: row.value for row in result.resolved}
    assert values["revenue"] == 1_250.0
    assert values["ebitda"] == 250.0

    # Every dependency is evaluated before the driver that references it.
    order = result.evaluation_order
    assert order.index("units") < order.index("revenue")
    assert order.index("price") < order.index("revenue")
    assert order.index("revenue") < order.index("ebitda")
    assert order.index("margin") < order.index("ebitda")

    by_name = {row.name: row for row in result.resolved}
    assert by_name["revenue"].depends_on == ["price", "units"]
    # Provenance carries the user note plus the transitive input closure.
    assert by_name["revenue"].provenance.note == "board deck"
    assert by_name["ebitda"].provenance.inputs == ["margin", "price", "revenue", "units"]
    assert by_name["units"].depends_on == [] and by_name["units"].provenance.inputs == []


def test_driver_model_rejects_a_direct_cycle_naming_the_path():
    request = DriverModelRequest.model_validate(
        {"drivers": [{"name": "a", "formula": "b + 1"}, {"name": "b", "formula": "a + 1"}]}
    )
    with pytest.raises(service.UnderwritingCalculationError, match="cycle") as exc:
        service.calculate_driver_model(request)
    message = str(exc.value)
    assert "a" in message and "b" in message and "->" in message


def test_driver_model_rejects_a_self_reference():
    request = DriverModelRequest.model_validate({"drivers": [{"name": "a", "formula": "a * 2"}]})
    with pytest.raises(service.UnderwritingCalculationError, match="cycle"):
        service.calculate_driver_model(request)


def test_driver_model_rejects_an_unknown_reference():
    request = DriverModelRequest.model_validate(
        {"drivers": [{"name": "revenue", "formula": "units * price"}]}
    )
    with pytest.raises(service.UnderwritingCalculationError, match="unknown driver"):
        service.calculate_driver_model(request)


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('echo hi')",  # function call
        "revenue.__class__",  # attribute access
        "abs(revenue)",  # function call on a builtin
        "revenue ** 2",  # unsupported operator (power)
        "[revenue]",  # subscript/list
    ],
)
def test_driver_model_rejects_unsafe_formulas(formula):
    request = DriverModelRequest.model_validate(
        {"drivers": [{"name": "revenue", "formula": "100"}, {"name": "x", "formula": formula}]}
    )
    with pytest.raises(service.UnderwritingCalculationError):
        service.calculate_driver_model(request)


def test_driver_model_endpoint_contract_and_cycle_is_422(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/driver-model"
    ok = client.post(url, json={"drivers": _drivers()})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert {row["name"] for row in body["resolved"]} == {
        "units",
        "price",
        "revenue",
        "margin",
        "ebitda",
    }

    cyclic = client.post(
        url,
        json={"drivers": [{"name": "a", "formula": "b"}, {"name": "b", "formula": "a"}]},
    )
    assert cyclic.status_code == 422

    unsafe = client.post(url, json={"drivers": [{"name": "a", "formula": "pow(2, 3)"}]})
    assert unsafe.status_code == 422


# --- G25 Working-capital seasonality ----------------------------------------------------------


def _twelve_months() -> list[dict]:
    # A clean seasonal shape: trough in month 1 (80), peak in month 8 (200).
    levels = [80, 90, 100, 120, 150, 170, 190, 200, 180, 150, 110, 95]
    return [{"month": index + 1, "value": value} for index, value in enumerate(levels)]


def test_seasonality_pegs_every_month_and_reports_peak_trough_amplitude():
    result = service.calculate_working_capital_seasonality(
        WorkingCapitalSeasonalityRequest.model_validate(
            {"monthly_working_capital": _twelve_months()}
        )
    )
    assert result.status == "complete"
    assert result.missing_months == []
    assert result.present_months == list(range(1, 13))
    assert len(result.monthly_pegs) == 12
    assert result.peak_month == 8
    assert result.trough_month == 1
    assert result.amplitude == 120.0  # 200 - 80
    assert result.annual_average == pytest.approx(136.25, abs=0.01)


def test_seasonality_averages_repeated_months_and_never_imputes_absent_ones():
    payload = WorkingCapitalSeasonalityRequest.model_validate(
        {
            "monthly_working_capital": [
                {"month": 1, "value": 100},
                {"month": 1, "value": 120},  # two January observations -> peg is their mean
                {"month": 6, "value": 200},
                # months 2-5, 7-12 absent: reported missing, never interpolated
            ]
        }
    )
    result = service.calculate_working_capital_seasonality(payload)
    assert result.status == "partial"
    january = next(entry for entry in result.monthly_pegs if entry.month == 1)
    assert january.peg == 110.0 and january.observation_count == 2
    assert result.present_months == [1, 6]
    assert result.missing_months == [2, 3, 4, 5, 7, 8, 9, 10, 11, 12]
    # Only the two present months feed the swing; absent months contribute nothing.
    assert result.peak_month == 6 and result.trough_month == 1
    assert len(result.monthly_pegs) == 2


def test_seasonality_endpoint_contract(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/working-capital-seasonality"
    response = client.post(url, json={"monthly_working_capital": _twelve_months()})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"
    assert body["peak_month"] == 8 and body["trough_month"] == 1

    partial = client.post(
        url, json={"monthly_working_capital": [{"month": 3, "value": 100}]}
    )
    assert partial.status_code == 200
    assert partial.json()["status"] == "partial"
    assert partial.json()["missing_months"] == [m for m in range(1, 13) if m != 3]


# --- G26 Dividend recap + bolt-on -------------------------------------------------------------


def test_dividend_recap_increases_leverage_and_lifts_irr_with_balanced_sources_uses():
    result = service.calculate_recap_boltons(
        RecapBoltOnRequest.model_validate(
            {
                "assumptions": sample_assumptions().model_dump(mode="json"),
                "events": [{"type": "dividend_recap", "period": "Y2", "amount": 150.0}],
            }
        )
    )
    # A recap draws debt to return capital early: exit leverage rises and IRR improves.
    assert result.adjusted.exit_debt > result.base.exit_debt
    assert result.leverage_delta is not None and result.leverage_delta > 0
    assert result.adjusted.irr is not None and result.base.irr is not None
    assert result.adjusted.irr > result.base.irr
    assert result.irr_delta is not None and result.irr_delta > 0
    # Pulling the dividend forward leaves nominal MoIC essentially unchanged.
    assert result.moic_delta == pytest.approx(0.0, abs=1e-6)
    # Every event's sources and uses reconcile exactly (Decimal-exact).
    assert result.sources_uses_balanced is True
    for event in result.events:
        assert event.balanced is True
        assert sum(Decimal(str(line.amount)) for line in event.sources) == sum(
            Decimal(str(line.amount)) for line in event.uses
        )


def test_bolt_on_adds_ebitda_and_returns_reflect_the_accretion():
    result = service.calculate_recap_boltons(
        RecapBoltOnRequest.model_validate(
            {
                "assumptions": sample_assumptions().model_dump(mode="json"),
                "events": [
                    {
                        "type": "bolt_on",
                        "period": "Y3",
                        "incremental_ebitda": 40.0,
                        "multiple_paid": 6.0,
                        "funded_by": "debt",
                    }
                ],
            }
        )
    )
    # Bought at 6x, held to a 10x exit -> accretive: exit EBITDA and MoIC both rise.
    assert result.adjusted.exit_ebitda == pytest.approx(result.base.exit_ebitda + 40.0, abs=0.01)
    assert result.adjusted.exit_debt == pytest.approx(result.base.exit_debt + 240.0, abs=0.01)
    assert result.moic_delta is not None and result.moic_delta > 0
    assert result.adjusted.moic > result.base.moic
    assert result.sources_uses_balanced is True
    # Reconcile the exit-equity uplift: +exit_multiple*EBITDA - acquisition debt.
    expected_uplift = 10.0 * 40.0 - 240.0
    assert result.adjusted.exit_equity_value == pytest.approx(
        result.base.exit_equity_value + expected_uplift, abs=0.02
    )


def test_equity_funded_bolt_on_adds_a_sponsor_outflow():
    result = service.calculate_recap_boltons(
        RecapBoltOnRequest.model_validate(
            {
                "assumptions": sample_assumptions().model_dump(mode="json"),
                "events": [
                    {
                        "type": "bolt_on",
                        "period": "Y2",
                        "incremental_ebitda": 30.0,
                        "multiple_paid": 7.0,
                        "funded_by": "equity",
                    }
                ],
            }
        )
    )
    # Equity funding does not add acquisition debt but injects an interim negative sponsor flow.
    assert result.adjusted.exit_debt == pytest.approx(result.base.exit_debt, abs=0.01)
    interim = [flow for flow in result.adjusted.cash_flows if flow["amount"] < 0][1:]
    assert interim, "expected an interim equity outflow"
    assert result.adjusted.exit_ebitda == pytest.approx(result.base.exit_ebitda + 30.0, abs=0.01)
    assert result.sources_uses_balanced is True


def test_recap_bolton_endpoint_and_unknown_period_is_422(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/recap-boltons"
    response = client.post(
        url,
        json={
            "assumptions": sample_assumptions().model_dump(mode="json"),
            "events": [{"type": "dividend_recap", "period": "Y1", "amount": 100.0}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["irr_delta"] is not None
    assert body["sources_uses_balanced"] is True

    bad_period = client.post(
        url,
        json={
            "assumptions": sample_assumptions().model_dump(mode="json"),
            "events": [{"type": "dividend_recap", "period": "Y9", "amount": 100.0}],
        },
    )
    assert bad_period.status_code == 422
