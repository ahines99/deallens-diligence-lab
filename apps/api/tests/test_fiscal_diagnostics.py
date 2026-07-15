"""G17 (ledger F41) — fiscal-period consistency diagnostics (offline, synthetic financials).

Every multi-operand derived metric must have used operands from the same reporting period;
mismatches are flagged with both periods named, never silently blended.
"""
from __future__ import annotations

from src.services import forensics_service as fx


def _src(value, end, fy, concept="X"):
    return {
        "value": value, "end": end, "accession": "acc", "form": "10-K",
        "concept": concept, "frame": "", "fy": fy,
    }


def _consistent_financials() -> dict:
    end, fy = "2024-12-31", "2024"
    return {
        "revenue": 100.0, "gross_profit": 60.0, "operating_income": 20.0, "net_income": 10.0,
        "rnd": 15.0, "cash": 50.0, "total_debt": 40.0,
        "gross_margin": 0.6, "operating_margin": 0.2, "net_margin": 0.1, "rnd_pct": 0.15,
        "sources": {
            "revenue": _src(100.0, end, fy, "Revenues"),
            "gross_profit": _src(60.0, end, fy, "GrossProfit"),
            "operating_income": _src(20.0, end, fy, "OperatingIncomeLoss"),
            "net_income": _src(10.0, end, fy, "NetIncomeLoss"),
            "rnd": _src(15.0, end, fy),
            "cash": _src(50.0, end, fy),
            "total_debt": _src(40.0, end, fy),
        },
    }


def test_same_period_operands_produce_empty_diagnostics():
    assert fx.fiscal_diagnostics(_consistent_financials()) == []


def test_diagnostics_none_when_no_source_points_stored():
    # Legacy workspace: headline numbers without per-metric source points → not computable.
    assert fx.fiscal_diagnostics({"revenue": 100.0, "net_margin": 0.1}) is None
    assert fx.fiscal_diagnostics(None) is None
    assert fx.fiscal_diagnostics({}) is None


def test_mismatched_period_operands_are_flagged_with_both_periods_named():
    fin = _consistent_financials()
    # net_margin stored as FY2025 net income over FY2024 revenue — a blended period.
    fin["sources"]["net_income"] = _src(11.0, "2025-12-31", "2025", "NetIncomeLoss")
    diags = fx.fiscal_diagnostics(fin)
    assert len(diags) == 1
    d = diags[0]
    assert d["metric"] == "net_margin"
    assert d["severity"] == "high"
    assert "FY2025" in d["period_a"] and "2025-12-31" in d["period_a"]
    assert "FY2024" in d["period_b"] and "2024-12-31" in d["period_b"]
    assert "net_income" in d["detail"] and "revenue" in d["detail"]


def test_end_date_drift_beyond_tolerance_is_flagged_even_without_fy_labels():
    fin = _consistent_financials()
    fin["sources"]["gross_profit"] = _src(60.0, "2024-09-30", "", "GrossProfit")
    fin["sources"]["revenue"]["fy"] = ""
    diags = fx.fiscal_diagnostics(fin)
    assert [d["metric"] for d in diags] == ["gross_margin"]
    # A few days of 52/53-week drift is NOT a mismatch.
    fin["sources"]["gross_profit"]["end"] = "2024-12-28"
    assert fx.fiscal_diagnostics(fin) == []


def test_underived_metrics_are_not_flagged():
    fin = _consistent_financials()
    fin["sources"]["net_income"] = _src(11.0, "2025-12-31", "2025")
    fin["net_margin"] = None  # nothing was derived, so nothing was blended
    assert fx.fiscal_diagnostics(fin) == []


def test_balance_sheet_instant_misaligned_with_period_end_is_flagged():
    fin = _consistent_financials()
    fin["sources"]["cash"] = _src(50.0, "2023-12-31", "2023")
    diags = fx.fiscal_diagnostics(fin)
    assert [d["metric"] for d in diags] == ["cash"]
    assert diags[0]["severity"] == "medium"


def test_mismatch_surfaces_as_risk_flag(monkeypatch):
    fin = _consistent_financials()
    fin["sources"]["net_income"] = _src(11.0, "2025-12-31", "2025")
    fin["forensic_inputs"] = {
        "years": ["2024"],
        "by_year": {"2024": {"revenue": 100.0, "net_income": 10.0, "assets": 500.0}},
    }

    class _Target:
        name = "Blended Co"
        fiscal_year_end = "2024-12-31"
        financials = fin

    monkeypatch.setattr(fx, "get_target", lambda session, wid: _Target())
    flags = fx.risk_flags(None, "ws")
    period_flags = [f for f in flags if "reporting periods" in f["title"]]
    assert len(period_flags) == 1
    flag = period_flags[0]
    assert flag["risk_category"] == "margin_pressure"
    assert flag["severity"] == "medium"
    assert "net_margin" in flag["finding"]
    assert "FY2025" in flag["finding"] and "FY2024" in flag["finding"]
    assert flag["evidence"]["claim_type"] == "calculation"


def test_forensics_endpoint_carries_fiscal_diagnostics(client):
    from src.db.session import SessionLocal
    from src.models import Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    fin = _consistent_financials()
    fin["sources"]["net_income"] = _src(11.0, "2025-12-31", "2025")
    fin["forensic_inputs"] = {
        "years": ["2024"],
        "by_year": {"2024": {"revenue": 100.0, "net_income": 10.0, "assets": 500.0}},
    }
    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="Diag Co", deal_type="buyout")
        )
        s.add(
            Target(
                workspace_id=ws.id,
                name="Diag Co",
                target_type="public_company",
                financials=fin,
            )
        )
        s.commit()
        wid = ws.id

    body = client.get(f"/api/workspaces/{wid}/forensics").json()
    assert "fiscal_diagnostics" in body
    diags = body["fiscal_diagnostics"]
    assert len(diags) == 1
    assert diags[0]["metric"] == "net_margin"
    assert "FY2025" in diags[0]["period_a"] and "FY2024" in diags[0]["period_b"]


def test_matching_end_date_never_flags_despite_fy_label_drift():
    """M3 regression: XBRL `fy` is the reporting filing's fiscal year, so a comparative instant
    retained from a newer 10-K can carry fy=N+1 while sharing the SAME period end. Identical end
    dates must never produce a mixed-period flag on the strength of the fy label alone."""
    fin = _consistent_financials()
    # Cash instant kept from the FY2025 filing (fy="2025") but dated the same 2024-12-31 period end.
    fin["sources"]["cash"] = _src(50.0, "2024-12-31", "2025")
    diagnostics = fx.fiscal_diagnostics(fin)
    assert diagnostics == [], f"identical end dates should not flag: {diagnostics}"
    # Sanity: a genuinely different end date on the same operand still flags.
    fin["sources"]["cash"] = _src(50.0, "2025-12-31", "2025")
    assert any(d["metric"] == "cash" for d in fx.fiscal_diagnostics(fin))
