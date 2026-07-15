"""G11 — 10-Q quarterly extraction + TTM derivation (offline, synthetic XBRL companyfacts).

TTM arithmetic across fiscal-year boundaries: exact sums over four contiguous discrete quarters,
Q4 derived as FY − (Q1+Q2+Q3) and labeled, and explicit null-with-reason (never a partial or
gap-spanning sum) when contiguity cannot be established.
"""
from __future__ import annotations

import pytest

from src.services import edgar_client, sec_financials


def _facts(concept_points: dict[str, list[dict]]) -> dict:
    return {
        "facts": {
            "us-gaap": {c: {"units": {"USD": pts}} for c, pts in concept_points.items()}
        }
    }


def _q(start, end, val, fy, fp, form="10-Q", accn="q", filed="2025-05-01"):
    return {
        "start": start, "end": end, "val": val, "fy": fy, "fp": fp,
        "form": form, "accn": accn, "filed": filed,
    }


def _fy(start, end, val, fy, accn="k", filed="2025-02-15"):
    return {
        "start": start, "end": end, "val": val, "fy": fy, "fp": "FY",
        "form": "10-K", "frame": f"CY{fy}", "accn": accn, "filed": filed,
    }


# --- quarterly_points -------------------------------------------------------


def test_quarterly_points_selects_quarter_durations_and_prefers_latest_filing():
    facts = _facts({"Revenues": [
        _q("2024-01-01", "2024-03-31", 100.0, 2024, "Q1", accn="orig", filed="2024-05-01"),
        # Same period restated in a later filing: amendment precedence keeps the restated value.
        _q("2024-01-01", "2024-03-31", 101.0, 2024, "Q1", accn="restated", filed="2025-05-01"),
        _q("2024-04-01", "2024-06-30", 110.0, 2024, "Q2"),
        # Annual duration and half-year duration must both be excluded.
        _fy("2024-01-01", "2024-12-31", 450.0, 2024),
        _q("2024-01-01", "2024-06-30", 210.0, 2024, "Q2", accn="h1"),
        # An instant-style point (no start) must be ignored.
        {"end": "2024-06-30", "val": 999.0, "fy": 2024, "fp": "Q2", "form": "10-Q"},
    ]})
    points = edgar_client.quarterly_points(facts, "Revenues")
    assert [(p["end"], p["val"], p["accn"]) for p in points] == [
        ("2024-03-31", 101.0, "restated"),
        ("2024-06-30", 110.0, "q"),
    ]


# --- TTM arithmetic ---------------------------------------------------------


def test_ttm_sums_four_contiguous_discrete_quarters_across_fy_boundary():
    # Q2'24 → Q1'25: crosses the 2024/2025 fiscal-year boundary.
    facts = _facts({"Revenues": [
        _q("2024-04-01", "2024-06-30", 100.0, 2024, "Q2"),
        _q("2024-07-01", "2024-09-30", 110.0, 2024, "Q3"),
        _q("2024-10-01", "2024-12-31", 120.0, 2024, "Q4", form="10-K"),
        _q("2025-01-01", "2025-03-31", 130.0, 2025, "Q1"),
    ]})
    out = sec_financials.extract_quarterly(facts)
    assert out["ttm"]["revenue"] == pytest.approx(460.0)
    basis = out["ttm_basis"]["revenue"]
    assert basis["reason"] is None
    assert [p["end"] for p in basis["periods"]] == [
        "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31",
    ]
    assert all(p["derivation"] is None for p in basis["periods"])


def test_ttm_derives_q4_from_fy_minus_q123_and_labels_it():
    # Q4'24 is not tagged discretely (the XBRL norm); it must come from FY − (Q1+Q2+Q3) = 125,
    # then the TTM (Q2'24..Q1'25) spans the fiscal-year boundary using the derived quarter.
    facts = _facts({"Revenues": [
        _q("2024-01-01", "2024-03-31", 100.0, 2024, "Q1"),
        _q("2024-04-01", "2024-06-30", 110.0, 2024, "Q2"),
        _q("2024-07-01", "2024-09-30", 115.0, 2024, "Q3"),
        _fy("2024-01-01", "2024-12-31", 450.0, 2024),
        _q("2025-01-01", "2025-03-31", 140.0, 2025, "Q1"),
    ]})
    out = sec_financials.extract_quarterly(facts)
    q4 = next(q for q in out["quarters"] if q["end"] == "2024-12-31")
    assert q4["revenue"] == pytest.approx(125.0)  # 450 − (100+110+115)
    assert q4["derived"] == {"revenue": "fy_minus_q123"}
    assert q4["start"] == "2024-10-01"
    assert out["ttm"]["revenue"] == pytest.approx(110.0 + 115.0 + 125.0 + 140.0)
    basis = out["ttm_basis"]["revenue"]
    assert basis["reason"] is None
    derived = {p["end"]: p["derivation"] for p in basis["periods"]}
    assert derived["2024-12-31"] == "fy_minus_q123"


def test_ttm_null_with_reason_when_a_quarter_is_missing():
    # Q3'24 missing and no FY fact to reconcile against: last four quarters have a gap.
    facts = _facts({"Revenues": [
        _q("2024-01-01", "2024-03-31", 100.0, 2024, "Q1"),
        _q("2024-04-01", "2024-06-30", 110.0, 2024, "Q2"),
        _q("2024-10-01", "2024-12-31", 120.0, 2024, "Q4", form="10-K"),
        _q("2025-01-01", "2025-03-31", 130.0, 2025, "Q1"),
    ]})
    out = sec_financials.extract_quarterly(facts)
    assert out["ttm"]["revenue"] is None  # never a partial sum
    reason = out["ttm_basis"]["revenue"]["reason"]
    assert reason is not None
    assert "not contiguous" in reason and "2024-06-30" in reason and "2024-10-01" in reason


def test_ttm_null_with_reason_when_fewer_than_four_quarters():
    facts = _facts({"Revenues": [
        _q("2024-07-01", "2024-09-30", 115.0, 2024, "Q3"),
        _q("2024-10-01", "2024-12-31", 120.0, 2024, "Q4", form="10-K"),
        _q("2025-01-01", "2025-03-31", 130.0, 2025, "Q1"),
    ]})
    out = sec_financials.extract_quarterly(facts)
    assert out["ttm"]["revenue"] is None
    assert "only 3 quarterly period(s)" in out["ttm_basis"]["revenue"]["reason"]


def test_ttm_null_on_fiscal_year_end_change_gap():
    # Fiscal-year-end change: after Q2 ending 2024-06-30 the next period starts 2024-09-01
    # (a two-month transition gap). The gap must produce null + reason, never a blend.
    facts = _facts({"Revenues": [
        _q("2023-10-01", "2023-12-31", 90.0, 2024, "Q1"),
        _q("2024-01-01", "2024-03-31", 100.0, 2024, "Q2"),
        _q("2024-04-01", "2024-06-30", 110.0, 2024, "Q3"),
        _q("2024-09-01", "2024-11-30", 105.0, 2025, "Q1"),
    ]})
    out = sec_financials.extract_quarterly(facts)
    assert out["ttm"]["revenue"] is None
    assert "not contiguous" in out["ttm_basis"]["revenue"]["reason"]


def test_q4_not_derived_when_fy_span_cannot_be_reconciled():
    # Only Q1 and Q3 are discrete: FY − quarters would span a hidden Q2 gap, so no derivation
    # may be produced and TTM stays null.
    facts = _facts({"Revenues": [
        _q("2024-01-01", "2024-03-31", 100.0, 2024, "Q1"),
        _q("2024-07-01", "2024-09-30", 115.0, 2024, "Q3"),
        _fy("2024-01-01", "2024-12-31", 450.0, 2024),
    ]})
    out = sec_financials.extract_quarterly(facts)
    assert all(not q["derived"] for q in out["quarters"])
    assert out["ttm"]["revenue"] is None


def test_extract_quarterly_covers_multiple_metrics_and_caps_quarters():
    revenue = [
        _q(f"{y}-{s}", f"{y}-{e}", v, y, fp)
        for (y, s, e, v, fp) in [
            (2023, "01-01", "03-31", 90.0, "Q1"), (2023, "04-01", "06-30", 92.0, "Q2"),
            (2023, "07-01", "09-30", 94.0, "Q3"), (2023, "10-01", "12-31", 96.0, "Q4"),
            (2024, "01-01", "03-31", 100.0, "Q1"), (2024, "04-01", "06-30", 110.0, "Q2"),
            (2024, "07-01", "09-30", 115.0, "Q3"), (2024, "10-01", "12-31", 125.0, "Q4"),
            (2025, "01-01", "03-31", 140.0, "Q1"),
        ]
    ]
    net_income = [_q("2025-01-01", "2025-03-31", 14.0, 2025, "Q1")]
    facts = _facts({"Revenues": revenue, "NetIncomeLoss": net_income})
    out = sec_financials.extract_quarterly(facts)
    assert len(out["quarters"]) == 8  # capped at the last 8 periods
    latest = out["quarters"][-1]
    assert latest["end"] == "2025-03-31"
    assert latest["revenue"] == 140.0 and latest["net_income"] == 14.0
    assert out["ttm"]["revenue"] == pytest.approx(110.0 + 115.0 + 125.0 + 140.0)
    assert out["ttm"]["net_income"] is None  # single NI quarter → explicit reason, no imputation
    assert out["ttm_basis"]["net_income"]["reason"] is not None
    assert out["ttm"]["gross_profit"] is None  # concept absent entirely


# --- endpoint contract ------------------------------------------------------


def _make_workspace_with_target(financials: dict | None) -> str:
    from src.db.session import SessionLocal
    from src.models import Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="Quarterly Co", deal_type="buyout")
        )
        s.add(
            Target(
                workspace_id=ws.id,
                name="Quarterly Co",
                target_type="public_company",
                financials=financials,
            )
        )
        s.commit()
        return ws.id


def test_quarterly_endpoint_contract(client):
    facts = _facts({"Revenues": [
        _q("2024-04-01", "2024-06-30", 100.0, 2024, "Q2"),
        _q("2024-07-01", "2024-09-30", 110.0, 2024, "Q3"),
        _q("2024-10-01", "2024-12-31", 120.0, 2024, "Q4", form="10-K"),
        _q("2025-01-01", "2025-03-31", 130.0, 2025, "Q1"),
    ]})
    wid = _make_workspace_with_target(
        {"quarterly": sec_financials.extract_quarterly(facts)}
    )
    body = client.get(f"/api/workspaces/{wid}/financials/quarterly").json()
    assert body["workspace_id"] == wid
    assert body["source_status"] == "available"
    assert body["ttm"]["revenue"] == pytest.approx(460.0)
    assert body["ttm"]["net_income"] is None
    assert body["ttm_basis"]["revenue"]["reason"] is None
    assert len(body["quarters"]) == 4
    assert body["quarters"][-1]["end"] == "2025-03-31"
    assert body["quarters"][-1]["derived"] == {}


def test_quarterly_endpoint_unavailable_before_refresh(client):
    # A workspace ingested before quarterly extraction existed has no stored key: the endpoint
    # must say so explicitly instead of returning a false-clean empty.
    wid = _make_workspace_with_target({"forensic_inputs": {"years": [], "by_year": {}}})
    body = client.get(f"/api/workspaces/{wid}/financials/quarterly").json()
    assert body["source_status"] == "unavailable"
    assert "refresh" in body["source_note"]
    assert body["quarters"] == []
    assert body["ttm"] == {
        "revenue": None, "gross_profit": None, "operating_income": None, "net_income": None,
    }
