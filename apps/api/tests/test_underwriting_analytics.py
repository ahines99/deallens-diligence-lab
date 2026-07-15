"""Offline tests for the Wave 4c underwriting analytics: covenant headroom (G23),
management-vs-sponsor case variance (G27), exit readiness (G28), and the valuation
football field (G30)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.schemas.underwriting_model import (
    CaseVarianceOperand,
    CaseVarianceRequest,
    UnderwritingAssumptions,
    ValuationTriangulationRequest,
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
    leverage_threshold: float = 4.0,
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
            "covenants": [
                {
                    "name": "Total leverage",
                    "metric": "total_leverage",
                    "test": "maximum",
                    "threshold": leverage_threshold,
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


@pytest.fixture(scope="module")
def workspace_id(client) -> str:
    response = client.post(
        "/api/workspaces", json={"name": "Underwriting Analytics", "deal_type": "buyout"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- G23 Covenant headroom -------------------------------------------------------------------


def test_covenant_headroom_breach_boundary_is_the_threshold_crossing_quarter():
    # Leverage opens above 3.5x and deleverages under full cash sweep, crossing the threshold
    # partway through the hold. The breach must end exactly at the crossing quarter.
    result = service.calculate_covenant_headroom(
        sample_assumptions(leverage_threshold=3.5, quarterly=True)
    )
    leverage = next(cov for cov in result.covenants if cov.metric == "total_leverage")
    assert leverage.breached is True
    assert leverage.first_breach_period == leverage.periods[0].period_label == "Q01"

    breached_flags = [period.breached for period in leverage.periods]
    # Deleveraging is monotone, so breaches form a contiguous prefix (all True then all False).
    assert breached_flags == sorted(breached_flags, reverse=True)
    # Every period's breach flag agrees with the sign of its (signed, compliant-positive) headroom.
    for period in leverage.periods:
        assert period.headroom is not None
        assert period.breached == (period.headroom < 0)

    crossing = breached_flags.index(False)
    assert crossing > 0
    last_breach, first_clear = leverage.periods[crossing - 1], leverage.periods[crossing]
    assert last_breach.breached is True and last_breach.headroom < 0
    assert first_clear.breached is False and first_clear.headroom >= 0


def test_covenant_headroom_endpoint_and_requires_a_covenant(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/covenant-headroom"
    response = client.post(url, json={"assumptions": sample_assumptions().model_dump(mode="json")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {cov["metric"] for cov in body["covenants"]} == {
        "total_leverage",
        "interest_coverage",
    }
    for covenant in body["covenants"]:
        assert len(covenant["periods"]) == 5

    no_covenants = sample_assumptions().model_dump(mode="json")
    no_covenants["covenants"] = []
    assert client.post(url, json={"assumptions": no_covenants}).status_code == 422


def test_covenant_headroom_rejects_invalid_assumptions(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/covenant-headroom"
    bad = sample_assumptions().model_dump(mode="json")
    bad["historical"]["ltm_revenue"] = -5.0
    assert client.post(url, json={"assumptions": bad}).status_code == 422


# --- G27 Case variance -----------------------------------------------------------------------


def test_case_variance_lines_reconcile_and_rank_by_materiality():
    result = service.calculate_case_variance(
        sample_assumptions(exit_multiple=11.0, annual_revenue_growth=0.12),
        sample_assumptions(),
        "management",
        "sponsor",
    )
    # Every line reconciles exactly: absolute_delta == management - sponsor (Decimal-exact).
    for line in result.lines:
        if line.management_value is not None and line.sponsor_value is not None:
            assert Decimal(str(line.absolute_delta)) == (
                Decimal(str(line.management_value)) - Decimal(str(line.sponsor_value))
            )
            if line.sponsor_value != 0:
                assert line.pct_delta == pytest.approx(
                    line.absolute_delta / line.sponsor_value, abs=1e-6
                )

    # Ranks are a dense 1..n permutation, ordered by descending absolute percentage materiality.
    assert [line.materiality_rank for line in result.lines] == list(
        range(1, len(result.lines) + 1)
    )
    ranked_pct = [line.pct_delta for line in result.lines if line.pct_delta is not None]
    assert ranked_pct == sorted(ranked_pct, key=lambda pct: -abs(pct))
    # Lines whose percentage delta is undefined always sort to the end.
    first_none = next(
        (i for i, line in enumerate(result.lines) if line.pct_delta is None), len(result.lines)
    )
    assert all(line.pct_delta is None for line in result.lines[first_none:])
    top = result.lines[0]
    assert top.pct_delta is not None
    assert abs(top.pct_delta) == max(abs(pct) for pct in ranked_pct)


def test_case_variance_endpoint_compares_persisted_cases(client, workspace_id):
    for case_key, growth in (("base", 0.08), ("upside", 0.12)):
        create = {
            "case_key": case_key,
            "assumptions": sample_assumptions(annual_revenue_growth=growth).model_dump(
                mode="json"
            ),
        }
        created = client.post(
            f"/api/workspaces/{workspace_id}/underwriting/cases", json=create
        )
        assert created.status_code == 201, created.text

    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/case-variance",
        json={
            "management": {"case_key": "upside"},
            "sponsor": {"case_key": "base"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["management_label"].startswith("upside")
    assert body["sponsor_label"].startswith("base")
    irr_line = next(line for line in body["lines"] if line["key"] == "irr")
    assert irr_line["management_value"] > irr_line["sponsor_value"]


def test_case_variance_operand_requires_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        CaseVarianceRequest(
            management=CaseVarianceOperand(),
            sponsor=CaseVarianceOperand(case_key="base"),
        )


# --- G28 Exit readiness ----------------------------------------------------------------------


def test_exit_readiness_scorecard_names_thresholds_and_grids_holds():
    result = service.calculate_exit_readiness(sample_assumptions())
    dimensions = {dimension.dimension: dimension for dimension in result.dimensions}
    assert set(dimensions) == {"leverage", "growth", "margin", "coverage"}
    for dimension in result.dimensions:
        # Each dimension names an explicit threshold and a scored rating.
        assert isinstance(dimension.threshold, float)
        assert 0.0 <= dimension.score <= 100.0
        assert dimension.rating in {"strong", "adequate", "weak", "insufficient_data"}
    assert 0.0 <= result.overall_score <= 100.0

    holds = result.hold_period_grid
    assert [point.hold_period_years for point in holds] == [3.0, 5.0, 7.0]
    for point in holds:
        assert point.irr is not None and 0.0 < point.irr < 1.0
        assert point.moic is not None
    # A longer hold compounds EBITDA and pays down debt, so MoIC rises monotonically with hold.
    assert holds[0].moic < holds[1].moic < holds[2].moic


def test_exit_readiness_endpoint(client, workspace_id):
    response = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/exit-readiness",
        json={"assumptions": sample_assumptions().model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["hold_period_grid"]) == 3
    assert body["overall_rating"] in {"strong", "adequate", "weak"}


# --- G30 Football field ----------------------------------------------------------------------


def test_football_field_weights_sum_to_one_and_excluded_methods_carry_reasons():
    result = service.calculate_football_field(
        ValuationTriangulationRequest.model_validate(
            {
                "ebitda": 100.0,
                "net_debt": 250.0,
                "dcf_enterprise_value": 1_100.0,
                "public_comps": [
                    {"name": "Peer A", "ev_ebitda_multiple": 9.0, "source": "Licensed feed"},
                    {"name": "Peer B", "ev_ebitda_multiple": 11.0, "source": "Licensed feed"},
                ],
                # No precedent transactions supplied -> excluded, never imputed.
            }
        )
    )
    assert [method.method for method in result.methods] == [
        "dcf",
        "public_comps",
        "precedent_transactions",
    ]
    included = [method for method in result.methods if method.included]
    excluded = [method for method in result.methods if not method.included]
    assert {method.method for method in excluded} == {"precedent_transactions"}

    assert sum(method.weight for method in included) == pytest.approx(1.0, abs=1e-6)
    assert result.included_weight_total == pytest.approx(1.0, abs=1e-6)
    for method in included:
        assert method.excluded_reason is None
        assert method.low is not None and method.mid is not None and method.high is not None
        assert method.low <= method.mid <= method.high
    for method in excluded:
        # Never impute a missing method: null bounds, zero weight, explicit reason.
        assert method.excluded_reason is not None and method.excluded_reason != ""
        assert method.low is None and method.mid is None and method.high is None
        assert method.weight == 0.0


def test_football_field_endpoint_and_requires_a_method(client, workspace_id):
    url = f"/api/workspaces/{workspace_id}/underwriting/football-field"
    response = client.post(
        url,
        json={
            "ebitda": 100.0,
            "net_debt": 0.0,
            "dcf_enterprise_value": 1_000.0,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["methods"]) == 3

    # No valuation method at all is rejected at request validation.
    assert client.post(url, json={"ebitda": 100.0, "net_debt": 0.0}).status_code == 422
