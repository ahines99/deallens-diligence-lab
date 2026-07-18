"""G65 — sum-of-the-parts valuation over G12 segment revenue (offline, synthetic facts).

The residual discipline under test: ``unallocated = consolidated - sum(segment revenues)`` is
always EXPLICIT, valued only when the request supplies a residual multiple, and the total is
never force-balanced to the consolidated figure. The G12 reconciliation status (``partial``)
propagates, and consolidated-only workspaces report ``unavailable``.
"""
from __future__ import annotations

import pytest

from src.services import sec_financials, sotp_service

_AXIS = "us-gaap:StatementBusinessSegmentsAxis"


def _facts(points: list[dict]) -> dict:
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": points}}}}}


def _annual(start, end, val, *, member=None):
    point = {"start": start, "end": end, "val": val, "form": "10-K", "accn": "a", "filed": "2025-02-15"}
    if member is not None:
        point["segments"] = [{"dim": _AXIS, "member": member}]
    return point


def _make_workspace(financials: dict | None) -> str:
    from src.db.session import SessionLocal
    from src.models import Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="SOTP Co", deal_type="buyout")
        )
        s.add(
            Target(
                workspace_id=ws.id,
                name="SOTP Co",
                target_type="public_company",
                financials=financials,
            )
        )
        s.commit()
        return ws.id


def _build(workspace_id: str, request: dict) -> dict:
    from src.db.session import SessionLocal

    with SessionLocal() as s:
        return sotp_service.build(s, workspace_id, request)


# Segments 600 + 400 reconcile exactly to consolidated 1000 (status "available").
_CLEAN_POINTS = [
    _annual("2023-01-01", "2023-12-31", 1000.0),
    _annual("2023-01-01", "2023-12-31", 600.0, member="xyz:CloudServicesMember"),
    _annual("2023-01-01", "2023-12-31", 400.0, member="xyz:HardwareMember"),
]
# Segments 600 + 300 vs consolidated 1200 (status "partial", untagged residual of 300).
_PARTIAL_POINTS = [
    _annual("2023-01-01", "2023-12-31", 1200.0),
    _annual("2023-01-01", "2023-12-31", 600.0, member="xyz:CloudServicesMember"),
    _annual("2023-01-01", "2023-12-31", 300.0, member="xyz:HardwareMember"),
]


def _financials(points: list[dict], *, revenue: float, fiscal_year_end: str = "2023-12-31") -> dict:
    return {
        "segments": sec_financials.extract_segments(_facts(points)),
        "revenue": revenue,
        "fiscal_year_end": fiscal_year_end,
    }


def test_sotp_arithmetic_hand_verified():
    wid = _make_workspace(_financials(_CLEAN_POINTS, revenue=1000.0))
    out = _build(wid, {
        "multiples": {"xyz:CloudServicesMember": 3.0, "xyz:HardwareMember": 2.0},
        "residual_multiple": 1.5,
    })
    assert out["status"] == "available"
    assert out["as_of_period"] == "2023-12-31"
    by_name = {row["segment_name"]: row for row in out["segments"]}
    # 600 x 3.0 = 1800; 400 x 2.0 = 800 — hand-verified.
    assert by_name["Cloud Services"]["implied_ev"] == 1800.0
    assert by_name["Hardware"]["implied_ev"] == 800.0
    assert by_name["Cloud Services"]["source"]["member"] == "xyz:CloudServicesMember"
    assert by_name["Cloud Services"]["source"]["concept"] == "Revenues"
    assert out["consolidated_revenue"] == 1000.0
    # Fully-tagged filer: the explicit residual is 1000 - (600+400) = 0, valued at 0 x 1.5 = 0.
    assert out["unallocated"] == {"revenue": 0.0, "multiple": 1.5, "implied_ev": 0.0}
    assert out["total_implied_ev"] == 2600.0


def test_sotp_residual_reported_but_unvalued_without_residual_multiple():
    wid = _make_workspace(_financials(_PARTIAL_POINTS, revenue=1200.0))
    out = _build(wid, {"multiples": {"xyz:CloudServicesMember": 3.0, "xyz:HardwareMember": 2.0}})
    # Residual = 1200 - (600+300) = 300, reported but NEVER valued without a residual multiple.
    assert out["unallocated"]["revenue"] == 300.0
    assert out["unallocated"]["multiple"] is None
    assert out["unallocated"]["implied_ev"] is None
    # Total is the valued parts only (1800 + 600) — never force-balanced toward consolidated.
    assert out["total_implied_ev"] == 2400.0
    assert "UNVALUED" in out["reconciliation_note"]


def test_sotp_residual_valued_only_with_residual_multiple():
    wid = _make_workspace(_financials(_PARTIAL_POINTS, revenue=1200.0))
    out = _build(wid, {
        "multiples": {"xyz:CloudServicesMember": 3.0, "xyz:HardwareMember": 2.0},
        "residual_multiple": 2.0,
    })
    assert out["unallocated"] == {"revenue": 300.0, "multiple": 2.0, "implied_ev": 600.0}
    assert out["total_implied_ev"] == 3000.0  # 1800 + 600 + 600


def test_sotp_partial_reconciliation_status_propagates():
    wid = _make_workspace(_financials(_PARTIAL_POINTS, revenue=1200.0))
    out = _build(wid, {"default_multiple": 2.0})
    assert out["status"] == "partial"
    assert "do not fully reconcile" in out["reconciliation_note"]


def test_sotp_consolidated_only_reports_unavailable():
    points = [
        _annual("2022-01-01", "2022-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 1200.0),
    ]
    wid = _make_workspace(_financials(points, revenue=1200.0))
    out = _build(wid, {"default_multiple": 2.0})
    assert out["status"] == "unavailable"
    assert out["segments"] == []
    assert out["total_implied_ev"] is None
    assert "consolidated only" in out["reconciliation_note"]


def test_sotp_unavailable_before_refresh():
    wid = _make_workspace({"revenue": 1200.0})  # no stored segments key (pre-G12 workspace)
    out = _build(wid, {"default_multiple": 2.0})
    assert out["status"] == "unavailable"
    assert "refresh" in out["reconciliation_note"]


def test_sotp_segment_without_multiple_stays_unvalued():
    wid = _make_workspace(_financials(_CLEAN_POINTS, revenue=1000.0))
    # Cloud matched by human segment name; Hardware has no multiple and no default.
    out = _build(wid, {"multiples": {"Cloud Services": 3.0}})
    by_name = {row["segment_name"]: row for row in out["segments"]}
    assert by_name["Cloud Services"]["implied_ev"] == 1800.0
    assert by_name["Hardware"]["multiple"] is None
    assert by_name["Hardware"]["implied_ev"] is None
    assert by_name["Hardware"]["revenue"] == 400.0  # revenue still reported
    assert out["total_implied_ev"] == 1800.0  # only the valued part
    assert "Hardware" in out["reconciliation_note"]


def test_sotp_default_multiple_fallback():
    wid = _make_workspace(_financials(_CLEAN_POINTS, revenue=1000.0))
    out = _build(wid, {"multiples": {"xyz:CloudServicesMember": 3.0}, "default_multiple": 1.0})
    by_name = {row["segment_name"]: row for row in out["segments"]}
    assert by_name["Cloud Services"]["implied_ev"] == 1800.0  # explicit beats default
    assert by_name["Hardware"]["implied_ev"] == 400.0  # 400 x default 1.0
    assert out["total_implied_ev"] == 2200.0


def test_sotp_missing_period_segment_blocks_residual_never_imputed():
    # Hardware reports only 2022; Cloud reports 2023. The as-of period is 2023-12-31 and the
    # residual is NOT computed from a partial segment sum — never imputed.
    points = [
        _annual("2023-01-01", "2023-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 600.0, member="xyz:CloudServicesMember"),
        _annual("2022-01-01", "2022-12-31", 350.0, member="xyz:HardwareMember"),
    ]
    wid = _make_workspace(_financials(points, revenue=1000.0))
    out = _build(wid, {"default_multiple": 2.0})
    by_name = {row["segment_name"]: row for row in out["segments"]}
    assert by_name["Hardware"]["revenue"] is None
    assert by_name["Hardware"]["implied_ev"] is None
    assert out["unallocated"]["revenue"] is None
    assert "lacks the as-of period" in out["reconciliation_note"]


def test_sotp_consolidated_from_trends_row_when_headline_period_differs():
    financials = _financials(_CLEAN_POINTS, revenue=1400.0, fiscal_year_end="2024-12-31")
    financials["trends"] = {"rows": [{"year": "2023", "revenue": 1000.0}]}
    wid = _make_workspace(financials)
    out = _build(wid, {"default_multiple": 2.0, "residual_multiple": 1.0})
    assert out["consolidated_revenue"] == 1000.0  # matched by year, not the mismatched headline
    assert out["unallocated"]["revenue"] == 0.0


def test_sotp_consolidated_unknown_reports_residual_uncomputable():
    financials = _financials(_CLEAN_POINTS, revenue=1400.0, fiscal_year_end="2024-12-31")
    wid = _make_workspace(financials)
    out = _build(wid, {"default_multiple": 2.0})
    assert out["consolidated_revenue"] is None
    assert out["unallocated"]["revenue"] is None
    assert "cannot be computed" in out["reconciliation_note"]


# --- endpoint contract -----------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _wire_router(client):
    """Mount the Wave 6 router (integrator wires it into main.py; no-op once that lands)."""
    from src.main import app
    from src.routers import research_wave6

    have = {getattr(r, "path", "") for r in app.routes}
    if "/api/workspaces/{workspace_id}/sotp" not in have:
        app.include_router(research_wave6.router)
    yield


def test_sotp_endpoint_contract(client):
    wid = _make_workspace(_financials(_CLEAN_POINTS, revenue=1000.0))
    resp = client.post(
        f"/api/workspaces/{wid}/sotp",
        json={
            "multiples": {"xyz:CloudServicesMember": 3.0, "xyz:HardwareMember": 2.0},
            "residual_multiple": 1.5,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == wid
    assert body["status"] == "available"
    assert body["total_implied_ev"] == 2600.0
    assert {row["segment_name"] for row in body["segments"]} == {"Cloud Services", "Hardware"}


def test_sotp_endpoint_rejects_nonpositive_multiple(client):
    wid = _make_workspace(_financials(_CLEAN_POINTS, revenue=1000.0))
    resp = client.post(f"/api/workspaces/{wid}/sotp", json={"multiples": {"Cloud Services": -1.0}})
    assert resp.status_code == 422


def test_sotp_endpoint_unknown_workspace_404(client):
    resp = client.post("/api/workspaces/nope/sotp", json={})
    assert resp.status_code == 404


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
