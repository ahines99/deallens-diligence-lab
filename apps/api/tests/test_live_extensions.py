"""Live integration tests for the roadmap extensions: trends, FRED macro, GovCon (skipped offline)."""
from __future__ import annotations


def test_trends_real(client, live_workspace_id):
    tr = client.get(f"/api/workspaces/{live_workspace_id}/trends").json()
    assert len(tr["years"]) >= 2
    assert len(tr["rows"]) == len(tr["years"])
    latest = tr["rows"][-1]
    assert latest["revenue"] and latest["revenue"] > 1e9
    assert tr["revenue_cagr"] is not None
    # Years are chronological strings.
    assert tr["years"] == sorted(tr["years"])


def test_macro_real(client, live_workspace_id):
    macro = client.get(f"/api/workspaces/{live_workspace_id}/macro").json()
    # At least one macro series should return (tolerate a transient FRED fetch failure).
    assert len(macro["series"]) >= 1
    for s in macro["series"]:
        assert s["points"]
        assert s["latest_value"] is not None
        assert s["series_id"]
    assert macro["commentary"]


def test_govcon_real(client, live_workspace_id):
    # Microsoft has substantial federal contract history.
    prof = client.post(f"/api/workspaces/{live_workspace_id}/govcon", json={}).json()
    assert prof["recipient_name"]
    assert prof["total_obligations"] >= 0
    assert isinstance(prof["agency_concentration"], list)
    if prof["total_obligations"] > 0:
        assert prof["agency_concentration"]
        top = prof["agency_concentration"][0]
        assert top["amount"] >= 0
        assert 0 <= (top["pct"] or 0) <= 1
    # recompete block is always shaped correctly
    assert "count" in prof["recompete"] and "awards" in prof["recompete"]

    # GET returns the persisted profile.
    got = client.get(f"/api/workspaces/{live_workspace_id}/govcon").json()
    assert got["recipient_name"] == prof["recipient_name"]


def test_govcon_evidence_resolves(client, live_workspace_id):
    """If GovCon produced findings, their citations must still resolve (faithfulness holds)."""
    client.post(f"/api/workspaces/{live_workspace_id}/govcon", json={})
    known = {e["ref"] for e in client.get(f"/api/workspaces/{live_workspace_id}/evidence").json()}
    for r in client.get(f"/api/workspaces/{live_workspace_id}/risks").json():
        if r["risk_category"] == "govcon_risk":
            assert r["evidence_ref"] in known
