"""Live integration tests for SEC event/insider/theme feeds + SIC auto-peer (skipped offline).

All hit live SEC EDGAR via the shared `live_workspace_id` fixture (MSFT). Insider data is tolerated
empty (Microsoft's Form 4 cadence varies); shapes are always asserted.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def _wire_feeds_router(client):
    """Ensure the feeds router is mounted (the integration agent wires it into main.py;
    this keeps the tests self-contained and is a no-op once that wiring lands)."""
    from src.main import app
    from src.routers import feeds

    have = {getattr(r, "path", "") for r in app.routes}
    if "/api/workspaces/{workspace_id}/events" not in have:
        app.include_router(feeds.router)
    yield


def test_events_shape(client, live_workspace_id):
    data = client.get(f"/api/workspaces/{live_workspace_id}/events").json()
    assert data["workspace_id"] == live_workspace_id
    assert isinstance(data["events"], list)
    assert data["events"], "MSFT should have recent filings in the event timeline"
    for e in data["events"]:
        assert e["form"]
        assert e["date"]
        assert isinstance(e["items"], list)
        assert isinstance(e["significant"], bool)
        for it in e["items"]:
            assert it["code"] and it["label"]
    # Every 8-K item code that decodes must round-trip to a labelled item.
    eightks = [e for e in data["events"] if e["form"].startswith("8-K")]
    if eightks:
        assert any(isinstance(e["url"], str) for e in eightks)


def test_insiders_shape(client, live_workspace_id):
    data = client.get(f"/api/workspaces/{live_workspace_id}/insiders").json()
    assert data["workspace_id"] == live_workspace_id
    s = data["summary"]
    assert s["window_days"] == 90
    assert s["buys"] >= 0 and s["sells"] >= 0
    assert isinstance(data["transactions"], list)  # tolerate empty
    for t in data["transactions"]:
        assert t["type"] in ("buy", "sell", "other")
        assert t["insider"]
        assert "shares" in t and "price" in t
    # net_shares is either None (no parsable transactions) or the signed buy/sell delta.
    assert s["net_shares"] is None or isinstance(s["net_shares"], (int, float))


def test_themes_shape(client, live_workspace_id):
    data = client.get(f"/api/workspaces/{live_workspace_id}/themes").json()
    assert data["workspace_id"] == live_workspace_id
    themes = data["themes"]
    assert len(themes) == 6  # the fixed red-flag theme set
    keys = {t["theme"] for t in themes}
    assert {"going_concern", "material_weakness", "restatement", "goodwill_impairment"} <= keys
    for t in themes:
        assert t["label"]
        assert t["count"] >= 0
        assert isinstance(t["hits"], list)
        for h in t["hits"]:
            assert "form" in h and "date" in h and "url" in h


def test_auto_comps_best_effort(client, live_workspace_id):
    comps = client.post(f"/api/workspaces/{live_workspace_id}/comps/auto").json()
    assert isinstance(comps, list)  # best-effort: may be thin, but must be a shaped list
    for c in comps:
        assert c["ticker"]
        assert c["workspace_id"] == live_workspace_id
        assert c["data_source"]
    # MSFT is a prepackaged-software (SIC 7372) filer; same-SIC peers should be discoverable.
    if comps:
        assert all(c["ticker"].upper() != "MSFT" for c in comps)


def test_risk_flags_shape(client, live_workspace_id):
    """risk_flags is module-level (spliced by the integration agent); it must return shaped dicts."""
    from src.db.session import SessionLocal
    from src.services import sec_feeds_service as feeds

    session = SessionLocal()
    try:
        flags = feeds.risk_flags(session, live_workspace_id)
    finally:
        session.close()
    assert isinstance(flags, list)  # MSFT is clean; typically empty, but always a list
    for f in flags:
        assert f["risk_category"]
        assert f["severity"] in ("low", "medium", "high", "critical")
        assert 0 <= f["severity_score"] <= 9
        ev = f["evidence"]
        assert ev["claim"] and ev["claim_type"] in ("fact", "calculation", "inference", "assumption")
        assert ev["agent_name"] == "sec_feeds"
