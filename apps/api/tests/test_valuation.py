"""Valuation & returns tests.

Offline unit tests exercise the pure LBO / WACC / DCF math (no DB, no network).
The live test hits SEC EDGAR (via the shared fixtures) and is skipped when offline.
"""
from __future__ import annotations

import math

import pytest

from src.services import valuation_service as vs

# The integration agent wires the valuation router into main.py. Until then, register it here so
# the live tests can exercise the real endpoints. No-op once main.py already includes it.
from src.main import app  # noqa: E402
from src.routers import valuation as _valuation_router  # noqa: E402

if not any(getattr(r, "path", "").endswith("/valuation") for r in app.routes):
    app.include_router(_valuation_router.router)


# --- LBO math (known inputs -> known IRR / MOIC) ----------------------------

def test_lbo_point_known_values():
    # EBITDA=100, 10x in / 10x out, 5x leverage, 5y hold, flat EBITDA, 50% FCF sweep.
    pt = vs._lbo_point(
        ebitda=100.0, entry_multiple=10.0, exit_multiple=10.0,
        leverage=5.0, hold_years=5, ebitda_cagr=0.0,
    )
    assert pt["entry_ev"] == 1000.0
    assert pt["entry_equity"] == 500.0  # 1000 - 500 debt
    assert pt["exit_ev"] == 1000.0
    # Debt paydown: 5 years * 50% * 100 EBITDA = 250 -> exit debt 250, exit equity 750.
    assert pt["exit_equity"] == 750.0
    assert pt["moic"] == 1.5
    assert pt["irr"] == round(1.5 ** (1 / 5) - 1, 4)  # ~0.0845


def test_lbo_growth_and_paydown():
    # With EBITDA growth, exit EBITDA and the FCF sweep both scale up.
    pt = vs._lbo_point(
        ebitda=200.0, entry_multiple=8.0, exit_multiple=9.0,
        leverage=4.0, hold_years=5, ebitda_cagr=0.10,
    )
    exit_ebitda = 200.0 * 1.10 ** 5
    assert pt["exit_ev"] == round(9.0 * exit_ebitda, 2)
    assert pt["entry_equity"] == round(8.0 * 200 - 4.0 * 200, 2)  # 1600 - 800 = 800
    # Multiple expansion + growth -> healthy IRR.
    assert pt["irr"] is not None and pt["irr"] > 0.1


def test_compute_lbo_sensitivity_grid_shape():
    inputs = {
        "entry_multiple": 10.0, "exit_multiple": 10.0, "leverage": 5.0,
        "hold_years": 5, "ebitda_cagr": 0.05,
    }
    res = vs.compute_lbo(100.0, inputs)
    sens = res["sensitivity"]
    assert len(sens["entry_multiples"]) == 5
    assert len(sens["exit_multiples"]) == 5
    # Axis spans center +/- 2 in unit steps.
    assert sens["entry_multiples"] == [8.0, 9.0, 10.0, 11.0, 12.0]
    assert sens["exit_multiples"] == [8.0, 9.0, 10.0, 11.0, 12.0]
    assert len(res["sensitivity"]["irr_grid"]) == 5
    assert all(len(row) == 5 for row in res["sensitivity"]["irr_grid"])
    assert all(len(row) == 5 for row in res["sensitivity"]["moic_grid"])
    # Center cell matches the headline scenario.
    assert res["sensitivity"]["moic_grid"][2][2] == res["moic"]
    # Higher exit multiple -> higher MOIC (row is monotonically non-decreasing in exit multiple).
    center_row = res["sensitivity"]["moic_grid"][2]
    assert center_row == sorted(center_row)
    assert res["assumptions"]


def test_compute_lbo_ebitda_none_returns_nulls_with_note():
    inputs = {
        "entry_multiple": 10.0, "exit_multiple": 12.0, "leverage": 5.0,
        "hold_years": 5, "ebitda_cagr": 0.05,
    }
    res = vs.compute_lbo(None, inputs)
    assert res["entry_ev"] is None
    assert res["moic"] is None and res["irr"] is None
    # Grid is still shaped, just all-null.
    assert len(res["sensitivity"]["irr_grid"]) == 5
    assert res["sensitivity"]["irr_grid"][0][0] is None
    assert any("n/a" in a.lower() for a in res["assumptions"])
    assert res["inputs"] == inputs


# --- WACC assembly ----------------------------------------------------------

def test_compute_wacc_known_values():
    # risk_free 4%, net_debt 200, equity 800 -> debt_weight 0.2.
    w = vs.compute_wacc(risk_free=0.04, net_debt=200.0, equity=800.0)
    assert w["cost_of_equity"] == 0.095  # 0.04 + 1.1*0.05
    assert w["cost_of_debt"] == 0.06     # 0.04 + 0.02
    assert w["debt_weight"] == 0.2
    # WACC = 0.8*0.095 + 0.2*0.06*(1-0.21) = 0.076 + 0.00948 = 0.08548
    assert w["value"] == pytest.approx(0.08548, abs=1e-9)
    assert w["equity_risk_premium"] == 0.05 and w["beta"] == 1.1 and w["tax_rate"] == 0.21


def test_compute_wacc_net_cash_clamps_to_all_equity():
    # Net-cash target (negative net debt) -> debt_weight clamps to 0, WACC == cost of equity.
    w = vs.compute_wacc(risk_free=0.04, net_debt=-500.0, equity=800.0)
    assert w["debt_weight"] == 0.0
    assert w["value"] == w["cost_of_equity"]


def test_compute_wacc_degrades_when_risk_free_missing():
    w = vs.compute_wacc(risk_free=None, net_debt=200.0, equity=800.0)
    assert w["value"] is None
    assert w["cost_of_equity"] is None and w["cost_of_debt"] is None
    # Labeled assumptions still present.
    assert w["equity_risk_premium"] == 0.05 and w["beta"] == 1.1


def test_compute_wacc_degrades_when_equity_missing():
    w = vs.compute_wacc(risk_free=0.04, net_debt=200.0, equity=None)
    assert w["debt_weight"] is None
    assert w["value"] is None


# --- DCF-lite ---------------------------------------------------------------

def test_compute_dcf_enterprise_value_positive_and_finite():
    d = vs.compute_dcf(fcf_base=100.0, wacc=0.10)
    ev = d["enterprise_value"]
    assert ev is not None and math.isfinite(ev) and ev > 0
    # A discounted growing perpetuity should exceed the undiscounted 5y FCF stream base.
    assert ev > 100.0
    assert d["growth"] == 0.05 and d["terminal_growth"] == 0.025
    assert d["assumptions"]


def test_compute_dcf_higher_wacc_lowers_ev():
    low = vs.compute_dcf(fcf_base=100.0, wacc=0.08)["enterprise_value"]
    high = vs.compute_dcf(fcf_base=100.0, wacc=0.12)["enterprise_value"]
    assert low > high


def test_compute_dcf_degrades_gracefully():
    # Gordon model undefined when wacc <= terminal growth.
    assert vs.compute_dcf(fcf_base=100.0, wacc=0.02)["enterprise_value"] is None
    # No FCF base -> no EV.
    assert vs.compute_dcf(fcf_base=None, wacc=0.10)["enterprise_value"] is None
    # No WACC -> no EV.
    assert vs.compute_dcf(fcf_base=100.0, wacc=None)["enterprise_value"] is None


# --- FCFF base (interest add-back is optional; CFO/capex are required) -------

def test_fcff_base_full_inputs_adds_back_after_tax_interest():
    val, omitted = vs._fcff_base(cfo=100.0, capex=30.0, interest=10.0)
    assert omitted is False
    assert val == pytest.approx(100.0 + 10.0 * (1 - vs.TAX_RATE) - 30.0)


def test_fcff_base_omits_untagged_interest_and_flags_it():
    # A cash-rich issuer that nets interest expense: CFO and capex present, interest untagged.
    # The add-back is omitted (FCFF = CFO - capex) and flagged for disclosure, not withheld.
    val, omitted = vs._fcff_base(cfo=100.0, capex=30.0, interest=None)
    assert omitted is True
    assert val == pytest.approx(70.0)


def test_fcff_base_requires_cfo_and_capex():
    # Material inputs are never imputed: a missing CFO or capex leaves FCFF n/a.
    assert vs._fcff_base(cfo=None, capex=30.0, interest=10.0) == (None, False)
    assert vs._fcff_base(cfo=100.0, capex=None, interest=10.0) == (None, False)


# --- Live integration (skipped offline) -------------------------------------

def test_valuation_real(client, live_workspace_id):
    val = client.get(f"/api/workspaces/{live_workspace_id}/valuation").json()
    assert val["target_name"]
    assert isinstance(val["notes"], list) and val["notes"]
    # WACC assumptions are always labeled, even if the computed value is n/a.
    wacc = val["wacc"]
    assert wacc["equity_risk_premium"] == 0.05
    assert wacc["beta"] == 1.1
    assert wacc["tax_rate"] == 0.21
    if wacc["risk_free"] is not None:
        assert 0 < wacc["risk_free"] < 0.2  # DGS10 as a decimal
        assert wacc["cost_of_equity"] is not None
    # DCF block is always shaped with its assumptions.
    assert val["dcf"]["growth"] == 0.05 and val["dcf"]["terminal_growth"] == 0.025
    assert val["dcf"]["assumptions"]
    # MSFT is a going concern with net cash — net_debt should be present (may be negative).
    assert val["net_debt"] is not None


def test_lbo_real(client, live_workspace_id):
    body = {
        "entry_multiple": 12.0, "exit_multiple": 12.0, "leverage": 5.0,
        "hold_years": 5, "ebitda_cagr": 0.08,
    }
    res = client.post(f"/api/workspaces/{live_workspace_id}/lbo", json=body).json()
    assert res["inputs"]["entry_multiple"] == 12.0
    sens = res["sensitivity"]
    assert len(sens["entry_multiples"]) == 5 and len(sens["exit_multiples"]) == 5
    assert len(sens["irr_grid"]) == 5 and len(sens["moic_grid"]) == 5
    assert res["assumptions"]
    # If EBITDA resolved (D&A tagged), returns are real numbers; otherwise cleanly null + note.
    if res["entry_ev"] is not None:
        assert res["moic"] is not None and res["irr"] is not None
        assert res["sensitivity"]["moic_grid"][2][2] == res["moic"]


def test_valuation_404_on_unknown_workspace(client):
    assert client.get("/api/workspaces/does-not-exist/valuation").status_code == 404
