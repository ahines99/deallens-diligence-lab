"""Forensics: offline score-math unit tests (synthetic by_year) + one live SEC test (MSFT).

The offline tests exercise the pure Altman/Piotroski/Beneish/accruals math and the risk_flags thresholds
against a hand-computed synthetic two-year fixture; they never touch the network. The live test hits SEC
EDGAR via the shared `live_workspace_id` fixture and is skipped when SEC is unreachable.
"""
from __future__ import annotations

import pytest

from src.services import forensics_service as fx

# --- synthetic two-year fixture (hand-computed expected values) -------------
# t = FY2024, prior = FY2023. Healthy, growing, cash-generative company.
_T = {
    "assets": 1000, "current_assets": 400, "current_liabilities": 200, "total_liabilities": 500,
    "receivables": 100, "inventory": 80, "payables": 60, "retained_earnings": 300, "equity": 500,
    "ppe_net": 300, "ltd": 250, "ltd_current": 50, "short_debt": 0, "cash": 120,
    "revenue": 800, "cogs": 480, "gross_profit": 320, "operating_income": 160, "net_income": 120,
    "cfo": 140, "capex": 40, "da": 50, "tax": 30, "interest": 20, "sga": 100,
    "shares_out": 1000, "shares_diluted": 1000, "sbc": 0, "rnd": 0,
}
_P = {
    "assets": 900, "current_assets": 350, "current_liabilities": 180, "total_liabilities": 480,
    "receivables": 90, "inventory": 70, "payables": 55, "retained_earnings": 220, "equity": 420,
    "ppe_net": 280, "ltd": 260, "ltd_current": 40, "short_debt": 0, "cash": 100,
    "revenue": 700, "cogs": 430, "gross_profit": 270, "operating_income": 130, "net_income": 95,
    "cfo": 110, "capex": 35, "da": 45, "tax": 25, "interest": 22, "sga": 95,
    "shares_out": 1000, "shares_diluted": 1000, "sbc": 0, "rnd": 0,
}


class _Target:
    def __init__(self, by_year, years, name="Synthetic Co", fye="2024-12-31"):
        self.name = name
        self.cash = by_year[years[-1]].get("cash")
        self.fiscal_year_end = fye
        self.financials = {"forensic_inputs": {"years": years, "by_year": by_year}}


def _fi(by_year, years):
    return {"years": years, "by_year": by_year}


def _score(core, key):
    return next(s for s in core["scores"] if s["key"] == key)


def test_altman_z_on_known_inputs():
    core = fx._core(_fi({"2023": _P, "2024": _T}, ["2023", "2024"]), _Target({"2024": _T}, ["2024"]))
    z = _score(core, "altman_z")
    assert z["available"] is True
    # 6.56*0.2 + 3.26*0.3 + 6.72*0.16 + 1.05*1.0 = 4.4152
    assert z["value"] == pytest.approx(4.415, abs=1e-3)
    assert z["rating"] == "strong"


def test_piotroski_full_nine():
    core = fx._core(_fi({"2023": _P, "2024": _T}, ["2023", "2024"]), _Target({"2024": _T}, ["2024"]))
    f = _score(core, "piotroski_f")
    assert f["available"] is True
    assert f["value"] == 9.0
    assert f["rating"] == "strong"
    assert len(f["components"]) == 9


def test_beneish_m_on_known_inputs():
    core = fx._core(_fi({"2023": _P, "2024": _T}, ["2023", "2024"]), _Target({"2024": _T}, ["2024"]))
    m = _score(core, "beneish_m")
    assert m["available"] is True
    assert m["value"] == pytest.approx(-2.455, abs=1e-3)
    assert m["rating"] == "neutral"  # -2.455 <= -1.78 -> no manipulation signal
    assert m["note"] is None


def test_beneish_depi_suppressed_when_da_missing():
    t = dict(_T, da=None)
    core = fx._core(_fi({"2023": _P, "2024": t}, ["2023", "2024"]), _Target({"2024": t}, ["2024"]))
    m = _score(core, "beneish_m")
    assert m["available"] is True
    assert m["note"] == "DEPI omitted (D&A untagged)"


def test_accruals_ratio():
    core = fx._core(_fi({"2023": _P, "2024": _T}, ["2023", "2024"]), _Target({"2024": _T}, ["2024"]))
    a = _score(core, "accruals")
    # (120 - 140) / 1000 = -0.02
    assert a["value"] == pytest.approx(-0.02, abs=1e-6)
    assert a["rating"] == "strong"


def test_qoe_core_metrics():
    core = fx._core(_fi({"2023": _P, "2024": _T}, ["2023", "2024"]), _Target({"2024": _T}, ["2024"]))
    qoe = {m["key"]: m["value"] for m in core["qoe"]}
    assert qoe["net_working_capital"] == 200
    assert qoe["fcf"] == 100  # 140 - 40
    assert qoe["ebitda"] == 210  # 160 + 50
    assert qoe["net_debt"] == 180  # (250 + 50 + 0) - 120
    assert qoe["interest_coverage"] == pytest.approx(8.0, abs=1e-6)  # 160 / 20


def test_ebitda_na_when_da_missing():
    t = dict(_T, da=None)
    core = fx._core(_fi({"2024": t}, ["2024"]), _Target({"2024": t}, ["2024"]))
    qoe = {m["key"]: m for m in core["qoe"]}
    assert qoe["ebitda"]["value"] is None
    assert qoe["leverage_nd_ebitda"]["value"] is None


def test_single_year_yoy_scores_na():
    core = fx._core(_fi({"2024": _T}, ["2024"]), _Target({"2024": _T}, ["2024"]))
    assert _score(core, "piotroski_f")["available"] is False
    assert _score(core, "beneish_m")["available"] is False
    assert _score(core, "altman_z")["available"] is True  # needs only t


def test_risk_flags_healthy_company_is_clean(monkeypatch):
    tgt = _Target({"2023": _P, "2024": _T}, ["2023", "2024"])
    monkeypatch.setattr(fx, "get_target", lambda session, wid: tgt)
    assert fx.risk_flags(None, "ws") == []


def test_risk_flags_distress_altman(monkeypatch):
    distress = {
        "assets": 1000, "current_assets": 100, "current_liabilities": 400, "total_liabilities": 950,
        "retained_earnings": -500, "equity": 50, "operating_income": -100, "net_income": -120,
        "cfo": -80, "capex": 20, "cash": 10, "ltd": 700, "ltd_current": 100, "short_debt": 0,
        "revenue": 300, "cogs": 260, "gross_profit": 40, "receivables": 60, "inventory": 40,
        "payables": 30, "ppe_net": 200, "da": 30, "tax": 0, "interest": 40, "sga": 80,
        "shares_out": 1000,
    }
    tgt = _Target({"2024": distress}, ["2024"], name="Distressed Co")
    monkeypatch.setattr(fx, "get_target", lambda session, wid: tgt)
    flags = fx.risk_flags(None, "ws")
    altman = [f for f in flags if "Altman" in f["title"]]
    assert altman, "expected an Altman distress flag"
    f = altman[0]
    assert f["risk_category"] == "debt_liquidity"
    assert f["severity"] == "high" and f["severity_score"] == 7
    assert f["workstream_owner"] == "financial"
    assert f["evidence"]["claim_type"] == "calculation"
    assert f["evidence"]["agent_name"] == "forensics_analyst"


def test_risk_flags_returns_valid_categories(monkeypatch):
    valid = {
        "customer_concentration", "supplier_concentration", "demand_weakness", "margin_pressure",
        "debt_liquidity", "legal_regulatory", "cyber_security", "integration_ma",
        "ai_tech_disruption", "govcon_risk",
    }
    manip = {
        "assets": 1000, "current_assets": 500, "current_liabilities": 200, "total_liabilities": 400,
        "retained_earnings": 400, "equity": 600, "operating_income": 150, "net_income": 200,
        "cfo": 10, "capex": 20, "cash": 100, "ltd": 100, "ltd_current": 0, "short_debt": 0,
        "revenue": 1000, "cogs": 300, "gross_profit": 700, "receivables": 400, "inventory": 50,
        "payables": 40, "ppe_net": 200, "da": None, "tax": 30, "interest": 10, "sga": 100,
        "shares_out": 1000,
    }
    prior = {
        "assets": 800, "current_assets": 400, "current_liabilities": 250, "total_liabilities": 400,
        "retained_earnings": 200, "equity": 400, "operating_income": 120, "net_income": 90,
        "cfo": 130, "capex": 20, "cash": 90, "ltd": 150, "ltd_current": 0, "short_debt": 0,
        "revenue": 700, "cogs": 200, "gross_profit": 500, "receivables": 150, "inventory": 60,
        "payables": 50, "ppe_net": 220, "da": None, "tax": 25, "interest": 12, "sga": 90,
        "shares_out": 1000,
    }
    tgt = _Target({"2023": prior, "2024": manip}, ["2023", "2024"], name="Aggressive Co")
    monkeypatch.setattr(fx, "get_target", lambda session, wid: tgt)
    flags = fx.risk_flags(None, "ws")
    for f in flags:
        assert f["risk_category"] in valid
        assert f["evidence"]["claim_type"] == "calculation"


# --- live SEC integration ---------------------------------------------------


def _ensure_router_mounted():
    """The integration agent wires the router into main.py; mount it here so the GET path is testable
    standalone (idempotent — no-op once main.py includes it)."""
    from src.main import app
    from src.routers import forensics as forensics_router

    if not any(getattr(r, "path", "").endswith("/forensics") for r in app.routes):
        app.include_router(forensics_router.router)


def test_forensics_live_msft(client, live_workspace_id):
    _ensure_router_mounted()
    resp = client.get(f"/api/workspaces/{live_workspace_id}/forensics")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "MICROSOFT" in body["target_name"].upper()
    assert body["as_of_year"] and len(body["as_of_year"]) == 4
    keys = {s["key"] for s in body["scores"]}
    assert {"altman_z", "piotroski_f", "beneish_m", "accruals"} <= keys
    # Microsoft is financially strong: Altman should compute and land safely.
    altman = next(s for s in body["scores"] if s["key"] == "altman_z")
    assert altman["available"] is True and altman["value"] is not None
    # QoE block is populated and every metric is well-shaped.
    assert body["qoe"]
    for m in body["qoe"]:
        assert m["unit"] in {"pct", "x", "usd", "days", "ratio"}
        assert "key" in m and "label" in m
    assert body["notes"]
