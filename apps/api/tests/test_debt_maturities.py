"""G16 — long-term-debt maturity schedule ("maturity wall") extraction (offline, synthetic XBRL).

Reads the us-gaap ``LongTermDebtMaturitiesRepaymentsOfPrincipal...`` per-year concepts into a
year-bucketed schedule. The load-bearing discipline is NEVER-IMPUTE: a bucket the filer did not tag
is reported in ``missing_buckets`` and omitted from the schedule — never zero-filled or
interpolated. No network access.
"""
from __future__ import annotations

import pytest

from src.services import forensics_service as fx
from src.services import sec_financials

C = {
    "Y1": "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    "Y2": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
    "Y3": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",
    "Y4": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",
    "Y5": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",
    "thereafter": "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
}


def _facts(concept_points: dict[str, list[dict]]) -> dict:
    return {
        "facts": {
            "us-gaap": {c: {"units": {"USD": pts}} for c, pts in concept_points.items()}
        }
    }


def _pt(end: str, val: float, filed: str = "2025-02-15", accn: str = "k") -> dict:
    return {"end": end, "val": val, "filed": filed, "accn": accn}


def _schedule_of(buckets: dict[str, float], end: str = "2024-12-31") -> dict:
    """Companyfacts carrying one point per named bucket at ``end``."""
    return _facts({C[b]: [_pt(end, v)] for b, v in buckets.items()})


# --- (a) all buckets tagged -------------------------------------------------


def test_all_buckets_tagged_yields_full_ordered_schedule_and_total():
    facts = _schedule_of(
        {"Y1": 100, "Y2": 200, "Y3": 300, "Y4": 400, "Y5": 500, "thereafter": 600}
    )
    out = sec_financials.extract_debt_maturities(facts)
    assert out["status"] == "available"
    assert out["as_of"] == "2024-12-31"
    assert out["missing_buckets"] == []
    assert [row["bucket"] for row in out["schedule"]] == [
        "Y1", "Y2", "Y3", "Y4", "Y5", "thereafter"
    ]
    assert [row["amount"] for row in out["schedule"]] == [100, 200, 300, 400, 500, 600]
    assert out["schedule"][0]["source_concept"] == C["Y1"]
    assert all(row["period_end"] == "2024-12-31" for row in out["schedule"])
    assert out["total_scheduled"] == pytest.approx(2100.0)


# --- (b) NEVER-IMPUTE: a missing bucket is reported, never zero/interpolated -


def test_missing_bucket_is_reported_and_never_imputed():
    # Y4 is simply not tagged. It must be absent from the schedule and named in missing_buckets,
    # and it must NOT be zero-filled or interpolated between Y3 (300) and Y5 (500).
    facts = _schedule_of({"Y1": 100, "Y2": 200, "Y3": 300, "Y5": 500, "thereafter": 600})
    out = sec_financials.extract_debt_maturities(facts)
    assert out["status"] == "partial"
    assert out["missing_buckets"] == ["Y4"]
    buckets = {row["bucket"] for row in out["schedule"]}
    assert "Y4" not in buckets  # never fabricated
    # No interpolated ~400 value snuck in; the tagged amounts are untouched.
    amounts = {row["bucket"]: row["amount"] for row in out["schedule"]}
    assert amounts == {"Y1": 100, "Y2": 200, "Y3": 300, "Y5": 500, "thereafter": 600}
    # total_scheduled sums ONLY tagged buckets — a zero-filled Y4 would not change it, an
    # interpolated one would. It is 1700, never 2100.
    assert out["total_scheduled"] == pytest.approx(1700.0)


# --- (c) no maturity concepts tagged → unavailable, not empty-clean ---------


def test_no_maturity_concepts_is_unavailable_not_clean_empty():
    facts = _facts({"Revenues": [_pt("2024-12-31", 9999)]})  # unrelated concept only
    out = sec_financials.extract_debt_maturities(facts)
    assert out["status"] == "unavailable"  # not a false-clean "available" with an empty schedule
    assert out["schedule"] == []
    assert out["as_of"] is None
    assert out["total_scheduled"] is None
    assert out["missing_buckets"] == ["Y1", "Y2", "Y3", "Y4", "Y5", "thereafter"]
    assert "not tagged" in out["note"]


# --- (d) partial status when only some buckets are present ------------------


def test_partial_status_when_only_some_buckets_present():
    facts = _schedule_of({"Y1": 700, "Y2": 300})
    out = sec_financials.extract_debt_maturities(facts)
    assert out["status"] == "partial"
    assert out["missing_buckets"] == ["Y3", "Y4", "Y5", "thereafter"]
    assert out["total_scheduled"] == pytest.approx(1000.0)
    assert out["note"] and "never zero-filled" in out["note"]


def test_only_latest_balance_sheet_date_populates_the_schedule():
    # A prior-year schedule (2023) must not blend into the current (2024) one.
    facts = _facts({
        C["Y1"]: [_pt("2023-12-31", 50, filed="2024-02-15"), _pt("2024-12-31", 100)],
        C["Y2"]: [_pt("2023-12-31", 60, filed="2024-02-15")],  # only tagged for the prior year
    })
    out = sec_financials.extract_debt_maturities(facts)
    assert out["as_of"] == "2024-12-31"
    assert [row["bucket"] for row in out["schedule"]] == ["Y1"]  # 2023 Y2 is not carried forward
    assert "Y2" in out["missing_buckets"]
    assert out["total_scheduled"] == pytest.approx(100.0)


def test_amendment_precedence_keeps_latest_filed_value():
    facts = _facts({
        C["Y1"]: [
            _pt("2024-12-31", 100, filed="2025-02-15", accn="orig"),
            _pt("2024-12-31", 111, filed="2025-08-01", accn="restated"),
        ],
    })
    out = sec_financials.extract_debt_maturities(facts)
    assert out["schedule"][0]["amount"] == pytest.approx(111.0)


# --- (e) endpoint contract incl. legacy unavailable path -------------------


def _make_workspace_with_target(financials: dict | None) -> str:
    from src.db.session import SessionLocal
    from src.models import Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="Maturity Co", deal_type="buyout")
        )
        s.add(
            Target(
                workspace_id=ws.id,
                name="Maturity Co",
                target_type="public_company",
                financials=financials,
            )
        )
        s.commit()
        return ws.id


def test_debt_maturities_endpoint_contract(client):
    facts = _schedule_of(
        {"Y1": 100, "Y2": 200, "Y3": 300, "Y5": 500, "thereafter": 600}  # Y4 untagged
    )
    wid = _make_workspace_with_target(
        {"debt_maturities": sec_financials.extract_debt_maturities(facts)}
    )
    body = client.get(f"/api/workspaces/{wid}/debt-maturities").json()
    assert body["workspace_id"] == wid
    assert body["source_status"] == "partial"
    assert body["missing_buckets"] == ["Y4"]
    assert "Y4" not in {row["bucket"] for row in body["schedule"]}
    assert body["total_scheduled"] == pytest.approx(1700.0)
    assert body["as_of"] == "2024-12-31"


def test_debt_maturities_endpoint_unavailable_before_refresh(client):
    # A workspace ingested before this feature has no stored key: explicit unavailable + refresh,
    # never a false-clean empty presented as "available".
    wid = _make_workspace_with_target({"forensic_inputs": {"years": [], "by_year": {}}})
    body = client.get(f"/api/workspaces/{wid}/debt-maturities").json()
    assert body["source_status"] == "unavailable"
    assert "refresh" in body["source_note"]
    assert body["schedule"] == []
    assert body["total_scheduled"] is None
    assert body["missing_buckets"] == []


# --- (f) near-term-wall risk flag ------------------------------------------

# A healthy single fiscal year: Altman computes safe, YoY scores are n/a — so no forensic flag
# fires and the maturity-wall flag can be asserted in isolation.
_HEALTHY = {
    "assets": 1000, "current_assets": 400, "current_liabilities": 200, "total_liabilities": 500,
    "receivables": 100, "inventory": 80, "payables": 60, "retained_earnings": 300, "equity": 500,
    "ppe_net": 300, "ltd": 250, "ltd_current": 50, "short_debt": 0, "cash": 120,
    "revenue": 800, "cogs": 480, "gross_profit": 320, "operating_income": 160, "net_income": 120,
    "cfo": 140, "capex": 40, "da": 50, "tax": 30, "interest": 20, "sga": 100, "shares_out": 1000,
}


class _Target:
    def __init__(self, financials, name="Levered Co", fye="2024-12-31"):
        self.name = name
        self.fiscal_year_end = fye
        self.financials = financials
        self.cash = None


def _financials_with_maturities(buckets: dict[str, float]) -> dict:
    facts = _schedule_of(buckets)
    return {
        "forensic_inputs": {"years": ["2024"], "by_year": {"2024": _HEALTHY}},
        "debt_maturities": sec_financials.extract_debt_maturities(facts),
    }


def test_near_term_maturity_wall_flag_fires_when_y1_y2_dominate(monkeypatch):
    fin = _financials_with_maturities(
        {"Y1": 600, "Y2": 500, "Y3": 100, "Y4": 50, "Y5": 50, "thereafter": 100}
    )
    assert fin["debt_maturities"]["status"] == "available"
    monkeypatch.setattr(fx, "get_target", lambda session, wid: _Target(fin))
    flags = fx.risk_flags(None, "ws")
    wall = [f for f in flags if "maturity wall" in f["title"].lower()]
    assert wall, "expected a near-term maturity-wall flag"
    f = wall[0]
    assert f["risk_category"] == "debt_liquidity"
    assert f["severity_score"] == 6
    assert f["evidence"]["claim_type"] == "calculation"
    assert f["evidence"]["agent_name"] == "forensics_analyst"


def test_no_maturity_wall_flag_when_back_loaded(monkeypatch):
    # Same total, but maturities are back-loaded: Y1+Y2 are a small share → no flag.
    fin = _financials_with_maturities(
        {"Y1": 50, "Y2": 50, "Y3": 100, "Y4": 100, "Y5": 100, "thereafter": 1000}
    )
    monkeypatch.setattr(fx, "get_target", lambda session, wid: _Target(fin))
    flags = fx.risk_flags(None, "ws")
    assert not [f for f in flags if "maturity wall" in f["title"].lower()]


def test_no_maturity_wall_flag_on_partial_schedule(monkeypatch):
    # An incomplete schedule (thereafter untagged) would overstate the near-term share, so the flag
    # must NOT fire on partial status even though Y1+Y2 are large relative to the tagged buckets.
    fin = _financials_with_maturities({"Y1": 600, "Y2": 500, "Y3": 100})
    assert fin["debt_maturities"]["status"] == "partial"
    monkeypatch.setattr(fx, "get_target", lambda session, wid: _Target(fin))
    flags = fx.risk_flags(None, "ws")
    assert not [f for f in flags if "maturity wall" in f["title"].lower()]
