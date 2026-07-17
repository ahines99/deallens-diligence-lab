"""Offline tests for the institutional underwriting workbench."""

from __future__ import annotations

import math
from datetime import date

import pytest

from src.schemas.underwriting_model import (
    ReverseStressRequest,
    SensitivityAxis,
    SensitivityRequest,
    UnderwritingAssumptions,
    UnderwritingCaseCreate,
    ValuationTriangulationRequest,
    WorkingCapitalPegRequest,
)
from src.services import underwriting_model_service as service

# The root integration registers this router in main.py. Keep focused tests independently runnable.
from src.main import app  # noqa: E402
from src.routers import underwriting_model as _underwriting_router  # noqa: E402

if not any("/underwriting/calculate" in getattr(route, "path", "") for route in app.routes):
    app.include_router(_underwriting_router.router)


def sample_assumptions(*, use_standard_periods: bool = False) -> UnderwritingAssumptions:
    periods = (
        []
        if use_standard_periods
        else [{"label": f"Y{year}", "months": 12} for year in range(1, 6)]
    )
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
                "exit_multiple": 10.0,
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
            "covenants": [
                {
                    "name": "Total leverage",
                    "metric": "total_leverage",
                    "test": "maximum",
                    "threshold": 4.0,
                },
                {
                    "name": "Interest coverage",
                    "metric": "interest_coverage",
                    "test": "minimum",
                    "threshold": 2.0,
                },
            ],
            "valuation": {
                "discount_rate": 0.10,
                "terminal_growth_rate": 0.025,
                "mid_year_convention": True,
            },
        }
    )


def test_standard_projection_is_monthly_y1_y2_then_annual_y3_y5():
    periods = service.standard_projection_periods(5.0)
    assert len(periods) == 27
    assert [period.label for period in periods[:2]] == ["M01", "M02"]
    assert all(period.months == 1 for period in periods[:24])
    assert [(period.label, period.months) for period in periods[-3:]] == [
        ("Y3", 12),
        ("Y4", 12),
        ("Y5", 12),
    ]


def test_sources_uses_balances_and_models_ownership():
    result = service.calculate_sources_uses(sample_assumptions())
    assert result["entry_enterprise_value"] == 2_000.0
    assert result["equity_purchase_price"] == 1_950.0
    assert result["balanced"] is True
    assert result["total_uses"] == result["total_sources"]
    assert result["sponsor_equity"] > 0
    assert 0 < result["sponsor_ownership"] < 1
    financing_fees = next(
        line["amount"] for line in result["uses"] if line["name"] == "Financing fees and OID"
    )
    assert financing_fees == 24.0


def test_integrated_projection_reconciles_pnl_cash_and_debt():
    assumptions = sample_assumptions()
    result = service.run_underwriting(assumptions)
    first = result.projection[0]
    expected_revenue = 1_000.0 * ((1.08**1.0) - 1.0) / math.log(1.08)
    assert first.revenue == pytest.approx(expected_revenue, abs=0.01)
    assert first.gross_profit == pytest.approx(first.revenue * 0.60, abs=0.01)
    assert first.revenue == pytest.approx(first.cost_of_goods_sold + first.gross_profit, abs=0.01)
    assert first.ebitda == pytest.approx(first.revenue * 0.20, abs=0.01)
    assert first.gross_profit == pytest.approx(first.operating_expenses + first.ebitda, abs=0.01)
    assert first.ebit == pytest.approx(first.ebitda - first.depreciation_amortization, abs=0.01)
    assert first.net_income == pytest.approx(first.earnings_before_tax - first.cash_taxes, abs=0.01)
    assert first.total_debt == pytest.approx(
        sum(tranche.ending_balance for tranche in first.debt_tranches), abs=0.02
    )
    for tranche in first.debt_tranches:
        assert tranche.ending_balance == pytest.approx(
            tranche.opening_balance
            + tranche.pik_interest
            + tranche.revolver_draw
            - tranche.paid_amortization
            - tranche.cash_sweep,
            abs=0.02,
        )
    first_lien = next(row for row in first.debt_tranches if row.name == "First Lien")
    assert first_lien.cash_rate == 0.09  # max(4% base, 5% floor) + 4% spread
    assert first.total_leverage == pytest.approx(
        first.total_debt / (first.ebitda / 1.0), abs=0.0001
    )
    assert first.liquidity >= first.ending_cash
    assert result.summary.first_covenant_breach == "Y1"
    assert result.summary.first_debt_service_default is None


def test_standard_monthly_projection_covers_exact_five_years():
    result = service.run_underwriting(sample_assumptions(use_standard_periods=True))
    assert len(result.projection) == 27
    assert sum(period.months for period in result.projection) == 60
    assert result.projection[0].start_date == date(2026, 1, 1)
    assert result.projection[-1].end_date == date(2030, 12, 31)


def test_revenue_projection_is_invariant_to_period_granularity():
    annual = service.run_underwriting(sample_assumptions())
    mixed = service.run_underwriting(sample_assumptions(use_standard_periods=True))
    assert sum(period.revenue for period in annual.projection) == pytest.approx(
        sum(period.revenue for period in mixed.projection), abs=0.10
    )
    assert annual.projection[-1].annualized_revenue == pytest.approx(
        mixed.projection[-1].annualized_revenue, abs=0.01
    )


def test_unfunded_maturity_is_reported_as_debt_service_default():
    data = sample_assumptions().model_dump(mode="json")
    data["projection"]["default_drivers"].update(
        {"ebitda_margin": 0.05, "capex_percent_revenue": 0.50}
    )
    data["debt_tranches"][1]["maturity_period"] = "Y1"
    result = service.run_underwriting(UnderwritingAssumptions.model_validate(data))
    assert result.summary.first_debt_service_default == "Y1"
    first_lien = next(row for row in result.projection[0].debt_tranches if row.name == "First Lien")
    assert first_lien.unpaid_amortization > 0
    assert result.projection[0].liquidity_shortfall > 0


def test_liquidity_deficit_carries_forward_as_negative_cash():
    """An unfunded shortfall must not be written off period to period (audit H1)."""
    data = sample_assumptions().model_dump(mode="json")
    # Burn cash hard: thin margins, heavy capex, no revolver headroom.
    data["projection"]["default_drivers"].update(
        {"ebitda_margin": 0.02, "capex_percent_revenue": 0.30}
    )
    data["debt_tranches"][0]["commitment"] = 10.0
    result = service.run_underwriting(UnderwritingAssumptions.model_validate(data))
    ending_cash = [period.ending_cash for period in result.projection]
    assert min(ending_cash) < 0, "deficit should surface as negative cash, not be floored at 0"
    # The deficit must carry into the next period's opening balance, not reset.
    first_negative = next(i for i, cash in enumerate(ending_cash) if cash < 0)
    if first_negative + 1 < len(result.projection):
        assert result.projection[first_negative + 1].beginning_cash == pytest.approx(
            ending_cash[first_negative], abs=0.01
        )
    # Downside liquidity is now allowed to go negative, so the portfolio watchlist
    # threshold (minimum_liquidity < 0) is reachable for engine-produced cases.
    assert result.summary.minimum_liquidity < 0
    # Net debt must reflect the deficit rather than a flattering zero-cash floor.
    worst = min(range(len(ending_cash)), key=lambda i: ending_cash[i])
    period = result.projection[worst]
    assert period.net_debt == pytest.approx(period.total_debt - period.ending_cash, abs=0.02)


def test_mid_year_convention_applies_to_terminal_value():
    """Terminal value gets the same mid-year timing as the explicit flows (audit M6)."""
    mid = service.run_underwriting(sample_assumptions())
    data = sample_assumptions().model_dump(mode="json")
    data["valuation"]["mid_year_convention"] = False
    year_end = service.run_underwriting(UnderwritingAssumptions.model_validate(data))
    assert mid.dcf.terminal_value == pytest.approx(year_end.dcf.terminal_value, abs=0.01)
    assert mid.dcf.pv_terminal_value == pytest.approx(
        year_end.dcf.pv_terminal_value * 1.10**0.5, abs=0.01
    )


def test_dcf_returns_and_xirr_are_economic_not_placeholder_values():
    result = service.run_underwriting(sample_assumptions())
    assert result.dcf.enterprise_value > 0
    assert result.dcf.equity_value == pytest.approx(
        result.dcf.enterprise_value - result.dcf.net_debt, abs=0.01
    )
    assert 0 < result.dcf.terminal_value_percent < 1
    assert result.returns.sponsor_invested_capital > 0
    assert result.returns.moic is not None and result.returns.moic > 0
    assert result.returns.xirr is not None
    assert service.xirr([(date(2020, 1, 1), -100.0), (date(2022, 1, 1), 121.0)]) == pytest.approx(
        0.10, abs=0.001
    )


def test_working_capital_peg_normalizes_seasonality_and_true_up():
    payload = WorkingCapitalPegRequest.model_validate(
        {
            "closing_date": "2026-12-31",
            "method": "seasonal_average",
            "delivered_working_capital": 125.0,
            "observations": [
                {
                    "observation_date": "2024-12-31",
                    "accounts_receivable": 150,
                    "inventory": 40,
                    "accounts_payable": 50,
                    "accrued_liabilities": 20,
                    "excluded_net_amount": 10,
                },
                {
                    "observation_date": "2025-06-30",
                    "accounts_receivable": 130,
                    "inventory": 30,
                    "accounts_payable": 50,
                    "accrued_liabilities": 20,
                },
                {
                    "observation_date": "2025-12-31",
                    "accounts_receivable": 160,
                    "inventory": 40,
                    "accounts_payable": 55,
                    "accrued_liabilities": 25,
                },
            ],
        }
    )
    result = service.calculate_working_capital_peg(payload)
    # December normalized NWC observations are 110 and 120.
    assert result.seasonal_average == 115.0
    assert result.peg == 115.0
    assert result.purchase_price_adjustment == 10.0


def test_sensitivity_and_reverse_stress_are_monotonic_and_solve_target():
    assumptions = sample_assumptions()
    sensitivity = service.calculate_sensitivity(
        SensitivityRequest(
            assumptions=assumptions,
            rows=SensitivityAxis(variable="entry_multiple", values=[9.0, 10.0, 11.0]),
            columns=SensitivityAxis(variable="exit_multiple", values=[9.0, 10.0, 11.0]),
            metric="irr",
        )
    )
    assert len(sensitivity.grid) == 3
    assert all(len(row) == 3 for row in sensitivity.grid)
    assert sensitivity.grid[1] == sorted(sensitivity.grid[1])
    assert [row[1] for row in sensitivity.grid] == sorted(
        [row[1] for row in sensitivity.grid], reverse=True
    )

    target = service.run_underwriting(assumptions).returns.xirr
    assert target is not None
    solved = service.calculate_reverse_stress(
        ReverseStressRequest(
            assumptions=assumptions,
            variable="exit_multiple",
            objective="irr",
            target=target,
            lower_bound=6.0,
            upper_bound=14.0,
        )
    )
    assert solved.status == "solved"
    assert solved.solved_value == pytest.approx(10.0, abs=0.01)
    assert solved.achieved_value == pytest.approx(target, abs=1e-5)


def test_valuation_triangulation_reconciles_dcf_public_and_precedent_methods():
    result = service.calculate_valuation_triangulation(
        ValuationTriangulationRequest.model_validate({
            "ebitda": 100.0,
            "net_debt": 250.0,
            "dcf_enterprise_value": 1_100.0,
            "public_comps": [
                {"name": "Peer A", "ev_ebitda_multiple": 9.0, "source": "Licensed feed"},
                {"name": "Peer B", "ev_ebitda_multiple": 11.0, "source": "Licensed feed"},
            ],
            "precedent_transactions": [
                {
                    "name": "Transaction A", "ev_ebitda_multiple": 12.0,
                    "source": "Advisor precedent set", "evidence_ref": "EV-101",
                }
            ],
        })
    )
    assert [method.method for method in result.methods] == [
        "dcf", "public_comps", "precedent_transactions"
    ]
    assert result.methods[1].enterprise_value_median == 1_000.0
    assert result.blended_enterprise_value == 1_090.0
    assert result.blended_equity_value == 840.0
    assert result.valuation_low == 900.0
    assert result.valuation_high == 1_200.0
    assert any("no evidence_ref" in warning for warning in result.warnings)


def test_case_versions_are_append_only_hashed_and_reviewable(client):
    workspace = client.post(
        "/api/workspaces", json={"name": "Underwriting API", "deal_type": "buyout"}
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["id"]
    create = UnderwritingCaseCreate(
        case_key="base",
        label="Base case",
        assumptions=sample_assumptions(),
        created_by="analyst@example.com",
        change_note="Initial underwrite",
    ).model_dump(mode="json")

    first_response = client.post(f"/api/workspaces/{workspace_id}/underwriting/cases", json=create)
    assert first_response.status_code == 201, first_response.text
    first = first_response.json()
    assert first["version"] == 1
    assert len(first["input_hash"]) == 64 and len(first["output_hash"]) == 64

    create["expected_parent_version"] = 1
    create["change_note"] = "Reviewed without economic changes"
    second_response = client.post(f"/api/workspaces/{workspace_id}/underwriting/cases", json=create)
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    assert second["version"] == 2
    assert second["parent_version_id"] == first["id"]
    assert second["input_hash"] == first["input_hash"]
    assert second["output_hash"] == first["output_hash"]

    stale = client.post(f"/api/workspaces/{workspace_id}/underwriting/cases", json=create)
    assert stale.status_code == 409

    submitted = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/cases/base/versions/2/decisions",
        json={
            "decision": "submitted",
            "actor": "analyst@example.com",
            "rationale": "Submitted for independent approval",
        },
    )
    assert submitted.status_code == 201, submitted.text
    decision = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/cases/base/versions/2/decisions",
        json={
            "decision": "approved",
            "actor": "ic-chair@example.com",
            "rationale": "Approved for IC circulation",
        },
    )
    assert decision.status_code == 201, decision.text
    latest = client.get(f"/api/workspaces/{workspace_id}/underwriting/cases/base")
    assert latest.status_code == 200
    assert latest.json()["latest_decision"]["decision"] == "approved"

    versions = client.get(f"/api/workspaces/{workspace_id}/underwriting/cases/base/versions").json()
    assert [version["version"] for version in versions] == [2, 1]

    from src.db.session import SessionLocal
    from src.models.underwriting_model import UnderwritingCaseVersion

    with SessionLocal() as session:
        persisted = session.get(UnderwritingCaseVersion, second["id"])
        persisted.label = "Attempted rewrite"
        with pytest.raises(ValueError, match="append-only"):
            session.commit()
        session.rollback()
