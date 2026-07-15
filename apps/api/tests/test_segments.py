"""G12 — XBRL segment-level revenue from dimensional facts (offline, synthetic companyfacts).

XBRL reality encoded here: standard SEC company facts publish only UNDIMENSIONED (consolidated)
points. Real filers therefore report "segment detail not available"; segment splits are never
fabricated. When a payload carries dimensional ``segments`` axis/member qualifiers (as these
synthetic fixtures do), the members are read back verbatim into per-segment period series.
"""
from __future__ import annotations

import pytest

from src.services import sec_financials


def _facts(concept_points: dict[str, list[dict]]) -> dict:
    return {
        "facts": {
            "us-gaap": {c: {"units": {"USD": pts}} for c, pts in concept_points.items()}
        }
    }


_AXIS = "us-gaap:StatementBusinessSegmentsAxis"


def _annual(start, end, val, *, member=None, axis=_AXIS, accn="a", filed="2025-02-15"):
    point = {"start": start, "end": end, "val": val, "form": "10-K", "accn": accn, "filed": filed}
    if member is not None:
        point["segments"] = [{"dim": axis, "member": member}]
    return point


# --- (a) dimensional members present → correct per-segment revenue by period -----------------


def test_extract_segments_reads_dimensional_members_by_period():
    facts = _facts({"RevenueFromContractWithCustomerExcludingAssessedTax": [
        # Consolidated totals (undimensioned) — must NOT be treated as a segment.
        _annual("2022-01-01", "2022-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 1200.0),
        # Cloud segment across two years.
        _annual("2022-01-01", "2022-12-31", 600.0, member="xyz:CloudServicesMember"),
        _annual("2023-01-01", "2023-12-31", 750.0, member="xyz:CloudServicesMember"),
        # Hardware segment across two years.
        _annual("2022-01-01", "2022-12-31", 400.0, member="xyz:HardwareMember"),
        _annual("2023-01-01", "2023-12-31", 450.0, member="xyz:HardwareMember"),
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["status"] == "available"
    assert out["axis"] == _AXIS
    by_name = {s["segment_name"]: s for s in out["segments"]}
    assert set(by_name) == {"Cloud Services", "Hardware"}
    cloud = by_name["Cloud Services"]
    assert cloud["member"] == "xyz:CloudServicesMember"
    assert cloud["source_concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert cloud["periods"] == [
        {"period_end": "2022-12-31", "revenue": 600.0},
        {"period_end": "2023-12-31", "revenue": 750.0},
    ]
    assert by_name["Hardware"]["periods"][-1] == {"period_end": "2023-12-31", "revenue": 450.0}


def test_extract_segments_prefers_latest_filed_point_per_period():
    facts = _facts({"Revenues": [
        _annual("2023-01-01", "2023-12-31", 700.0, member="xyz:CloudServicesMember",
                accn="orig", filed="2024-02-15"),
        # Same period restated in a later filing: amendment precedence keeps the restated value.
        _annual("2023-01-01", "2023-12-31", 725.0, member="xyz:CloudServicesMember",
                accn="restated", filed="2025-02-15"),
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["segments"][0]["periods"] == [{"period_end": "2023-12-31", "revenue": 725.0}]


# --- (b) consolidated-only facts → never fabricated -----------------------------------------


def test_extract_segments_consolidated_only_reports_unavailable():
    facts = _facts({"RevenueFromContractWithCustomerExcludingAssessedTax": [
        _annual("2022-01-01", "2022-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 1200.0),
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["status"] == "unavailable"
    assert out["segments"] == []  # no fabricated splits
    assert out["axis"] is None
    assert "consolidated only" in out["note"]


def test_extract_segments_ignores_cross_tabulated_cells():
    # A point qualified on two axes at once (segment x geography) is a finer cross-tab; counting it
    # would double-count, so it is skipped and, with no clean single-axis member, stays unavailable.
    facts = _facts({"Revenues": [
        _annual("2023-01-01", "2023-12-31", 1200.0),
        {
            "start": "2023-01-01", "end": "2023-12-31", "val": 300.0, "form": "10-K",
            "accn": "a", "filed": "2025-02-15",
            "segments": [
                {"dim": _AXIS, "member": "xyz:CloudServicesMember"},
                {"dim": "us-gaap:StatementGeographicalAxis", "member": "xyz:AmericasMember"},
            ],
        },
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["status"] == "unavailable"
    assert out["segments"] == []


def test_extract_segments_partial_when_members_do_not_reconcile():
    # Tagged members sum to 900 but the consolidated total is 1200 (an untagged Other/eliminations
    # member): honest "partial", still never inventing the missing 300.
    facts = _facts({"Revenues": [
        _annual("2023-01-01", "2023-12-31", 1200.0),
        _annual("2023-01-01", "2023-12-31", 600.0, member="xyz:CloudServicesMember"),
        _annual("2023-01-01", "2023-12-31", 300.0, member="xyz:HardwareMember"),
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["status"] == "partial"
    assert "do not fully reconcile" in out["note"]
    assert sum(s["periods"][-1]["revenue"] for s in out["segments"]) == 900.0  # not fabricated to 1200


# --- (d) segment period trend ordering ------------------------------------------------------


def test_extract_segments_orders_periods_oldest_first_and_caps():
    points = [
        _annual(f"{y}-01-01", f"{y}-12-31", float(v), member="xyz:CloudServicesMember")
        # Deliberately out of order to prove sorting.
        for y, v in [(2024, 90), (2020, 50), (2022, 70), (2019, 40),
                     (2023, 80), (2021, 60), (2018, 30)]
    ]
    facts = _facts({"Revenues": points})
    out = sec_financials.extract_segments(facts, n=6)
    periods = out["segments"][0]["periods"]
    ends = [p["period_end"] for p in periods]
    assert ends == sorted(ends)  # oldest first
    assert len(periods) == 6  # capped at the last 6 periods (2018 dropped)
    assert ends[0] == "2019-12-31" and ends[-1] == "2024-12-31"


def test_extract_segments_excludes_quarterly_durations():
    # A quarterly-length dimensional point must not be mistaken for an annual segment figure.
    facts = _facts({"Revenues": [
        _annual("2023-01-01", "2023-03-31", 150.0, member="xyz:CloudServicesMember"),  # ~90d quarter
        _annual("2023-01-01", "2023-12-31", 600.0, member="xyz:CloudServicesMember"),  # annual
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["segments"][0]["periods"] == [{"period_end": "2023-12-31", "revenue": 600.0}]


# --- (c) endpoint contract, including the legacy-workspace unavailable path ------------------


def _make_workspace_with_target(financials: dict | None) -> str:
    from src.db.session import SessionLocal
    from src.models import Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="Segment Co", deal_type="buyout")
        )
        s.add(
            Target(
                workspace_id=ws.id,
                name="Segment Co",
                target_type="public_company",
                financials=financials,
            )
        )
        s.commit()
        return ws.id


def test_segments_endpoint_contract(client):
    facts = _facts({"Revenues": [
        _annual("2022-01-01", "2022-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 1350.0),
        _annual("2022-01-01", "2022-12-31", 600.0, member="xyz:CloudServicesMember"),
        _annual("2023-01-01", "2023-12-31", 900.0, member="xyz:CloudServicesMember"),
        _annual("2022-01-01", "2022-12-31", 400.0, member="xyz:HardwareMember"),
        _annual("2023-01-01", "2023-12-31", 450.0, member="xyz:HardwareMember"),
    ]})
    wid = _make_workspace_with_target({"segments": sec_financials.extract_segments(facts)})
    body = client.get(f"/api/workspaces/{wid}/financials/segments").json()
    assert body["workspace_id"] == wid
    assert body["source_status"] == "available"
    assert body["axis"] == _AXIS
    names = {s["segment_name"] for s in body["segments"]}
    assert names == {"Cloud Services", "Hardware"}
    cloud = next(s for s in body["segments"] if s["segment_name"] == "Cloud Services")
    assert cloud["periods"][-1] == {"period_end": "2023-12-31", "revenue": 900.0}


def test_segments_endpoint_unavailable_before_refresh(client):
    # A workspace ingested before segment extraction existed has no stored key: the endpoint must
    # say so explicitly (refresh required) instead of returning a false-clean empty.
    wid = _make_workspace_with_target({"forensic_inputs": {"years": [], "by_year": {}}})
    body = client.get(f"/api/workspaces/{wid}/financials/segments").json()
    assert body["source_status"] == "unavailable"
    assert "refresh" in body["source_note"]
    assert body["segments"] == []
    assert body["axis"] is None


def test_segments_endpoint_consolidated_only(client):
    facts = _facts({"Revenues": [
        _annual("2022-01-01", "2022-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 1200.0),
    ]})
    wid = _make_workspace_with_target({"segments": sec_financials.extract_segments(facts)})
    body = client.get(f"/api/workspaces/{wid}/financials/segments").json()
    assert body["source_status"] == "unavailable"
    assert "consolidated only" in body["source_note"]
    assert body["segments"] == []


def test_extract_segments_partial_reconcile_matches_within_tolerance():
    # Rounding-level gaps must not trip the partial flag.
    facts = _facts({"Revenues": [
        _annual("2023-01-01", "2023-12-31", 1000.0),
        _annual("2023-01-01", "2023-12-31", 600.0, member="xyz:CloudServicesMember"),
        _annual("2023-01-01", "2023-12-31", 401.0, member="xyz:HardwareMember"),  # sum 1001 vs 1000
    ]})
    out = sec_financials.extract_segments(facts)
    assert out["status"] == "available"
    assert out["note"] is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
