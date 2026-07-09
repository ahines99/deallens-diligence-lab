"""Live integration tests against real SEC EDGAR data (skipped when offline).

Uses MSFT (stable large-cap). Assertions tolerate real-number variation — they check shape, sign,
ranges, and citation integrity rather than exact figures.
"""
from __future__ import annotations

from src.schemas.common import ClaimType, Priority, RiskCategory, Severity, Workstream

CATS = set(RiskCategory.__args__)
SEVS = set(Severity.__args__)
WS = set(Workstream.__args__)
PRIOS = set(Priority.__args__)
CLAIMS = set(ClaimType.__args__)


def test_target_is_real(client, live_workspace_id):
    ov = client.get(f"/api/workspaces/{live_workspace_id}").json()
    t = ov["target"]
    assert t is not None
    assert t["ticker"] == "MSFT"
    assert t["cik"]
    assert t["is_synthetic"] is False
    assert "EDGAR" in t["data_source"]
    assert t["revenue"] and t["revenue"] > 1e9  # real, large
    assert t["gross_margin"] and 0 < t["gross_margin"] < 1
    assert t["revenue_growth"] is not None

    assert ov["counts"]["filings"] >= 1
    filings = client.get(f"/api/workspaces/{live_workspace_id}/filings").json()
    assert any(f["form_type"] == "10-K" for f in filings)
    assert all(f["is_synthetic"] is False for f in filings)
    assert any(f["document_url"] and "sec.gov" in f["document_url"] for f in filings)


def test_risks_real(client, live_workspace_id):
    risks = client.get(f"/api/workspaces/{live_workspace_id}/risks").json()
    assert len(risks) >= 3
    scores = [r["severity_score"] for r in risks]
    assert scores == sorted(scores, reverse=True)
    for r in risks:
        assert r["risk_category"] in CATS
        assert r["severity"] in SEVS
        assert 1 <= r["severity_score"] <= 10
        assert r["workstream_owner"] in WS
        assert r["evidence_ref"]  # every finding is cited


def test_questions_real(client, live_workspace_id):
    qs = client.get(f"/api/workspaces/{live_workspace_id}/questions").json()
    assert len(qs) >= 8
    for q in qs:
        assert q["workstream"] in WS
        assert q["priority"] in PRIOS
    assert len({q["workstream"] for q in qs}) >= 4


def test_memo_real(client, live_workspace_id):
    memo = client.get(f"/api/workspaces/{live_workspace_id}/memo").json()
    assert memo["memo_type"] == "ic_memo"
    c = memo["markdown_content"]
    assert "not investment advice" in c.lower()
    assert "EV-" in c
    assert "MSFT" in c or "Microsoft" in c


def test_red_team_real(client, live_workspace_id):
    rt = client.get(f"/api/workspaces/{live_workspace_id}/red-team").json()
    assert rt["bear_case_markdown"].strip()
    assert len(rt["unsupported_claims"]) >= 1
    assert len(rt["missing_evidence"]) >= 1
    assert len(rt["high_priority_questions"]) >= 1
    assert "not investment advice" in rt["bear_case_markdown"].lower()

    ov = client.get(f"/api/workspaces/{live_workspace_id}").json()
    assert ov["artifacts"]["bear_case"] is True


def test_evidence_real(client, live_workspace_id):
    ev = client.get(f"/api/workspaces/{live_workspace_id}/evidence").json()
    assert len(ev) >= 5
    refs = [e["ref"] for e in ev]
    assert len(refs) == len(set(refs))
    for e in ev:
        assert e["claim_type"] in CLAIMS
        assert e["source_type"] in ("xbrl", "sec_filing", "usaspending")
    # Real financials produce both facts and calculations.
    assert {"fact", "calculation"} <= {e["claim_type"] for e in ev}


def test_benchmark_real(client, live_workspace_id):
    comps = client.post(
        f"/api/workspaces/{live_workspace_id}/comps", json={"tickers": ["ORCL", "CRM"]}
    ).json()
    assert len(comps) >= 1
    assert all(c["is_illustrative"] is False for c in comps)
    assert all("EDGAR" in c["data_source"] for c in comps)

    bench = client.get(f"/api/workspaces/{live_workspace_id}/benchmark").json()
    assert bench["peer_count"] == len(comps)
    keys = {m["key"] for m in bench["metrics"]}
    assert {"revenue", "gross_margin", "rule_of_40"} <= keys
    # Market multiples are intentionally omitted (no fabricated valuation data).
    assert "ev_revenue" not in keys
    rev = next(m for m in bench["metrics"] if m["key"] == "revenue")
    assert rev["target_value"] and rev["peer_median"]
