"""Async workspace-build lifecycle: deferred ingest, live step progress, failure, and retry.

Runs fully offline: EDGAR resolution and ingestion are monkeypatched so the tests exercise
the build state machine, not the network.
"""
from __future__ import annotations

import pytest

from src.services import workspace_service


FAKE_INFO = {"cik": "0000000000", "ticker": "FAKE", "name": "Fake Example Corp"}


@pytest.fixture()
def offline_build(monkeypatch):
    """Patch the network-bound stages so a ticker build succeeds deterministically."""
    from src.services import analysis_service, sec_ingestion_service

    monkeypatch.setattr(
        workspace_service.edgar_client, "resolve_ticker", lambda ticker: FAKE_INFO
    )

    def fake_ingest(session, workspace_id, ticker, filing_limit=8, progress=None):
        if progress is not None:
            progress("resolving_company")
            progress("fetching_financials")
        return None

    monkeypatch.setattr(sec_ingestion_service, "ingest_company", fake_ingest)
    monkeypatch.setattr(analysis_service, "run_full_analysis", lambda session, ws_id: None)


def test_create_with_ticker_builds_in_background_and_reports_ready(client, offline_build):
    resp = client.post("/api/workspaces", json={"ticker": "FAKE", "deal_type": "public_equity"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Fake Example Corp (FAKE) Diligence"
    # TestClient executes background tasks before returning, so the build has completed.
    status = client.get(f"/api/workspaces/{body['id']}/build-status").json()
    assert status == {
        "workspace_id": body["id"],
        "status": "ready",
        "step": None,
        "error": None,
        "ticker": "FAKE",
    }


def test_create_without_ticker_is_immediately_ready(client):
    resp = client.post(
        "/api/workspaces", json={"name": "Private Co", "deal_type": "buyout"}
    )
    assert resp.status_code == 201, resp.text
    status = client.get(f"/api/workspaces/{resp.json()['id']}/build-status").json()
    assert status["status"] == "ready"
    assert status["ticker"] is None


def test_failed_build_records_error_and_retry_rearms(client, offline_build, monkeypatch):
    from src.services import sec_ingestion_service

    calls = {"count": 0}

    def flaky_ingest(session, workspace_id, ticker, filing_limit=8, progress=None):
        calls["count"] += 1
        if progress is not None:
            progress("fetching_financials")
        if calls["count"] == 1:
            raise RuntimeError("EDGAR timed out mid-ingest")

    monkeypatch.setattr(sec_ingestion_service, "ingest_company", flaky_ingest)

    resp = client.post("/api/workspaces", json={"ticker": "FAKE", "deal_type": "public_equity"})
    assert resp.status_code == 201, resp.text
    workspace_id = resp.json()["id"]

    failed = client.get(f"/api/workspaces/{workspace_id}/build-status").json()
    assert failed["status"] == "failed"
    assert "EDGAR timed out" in failed["error"]
    # The step where the failure happened is retained for diagnosis.
    assert failed["step"] == "fetching_financials"

    retried = client.post(f"/api/workspaces/{workspace_id}/build/retry")
    assert retried.status_code == 200, retried.text
    final = client.get(f"/api/workspaces/{workspace_id}/build-status").json()
    assert final == {
        "workspace_id": workspace_id,
        "status": "ready",
        "step": None,
        "error": None,
        "ticker": "FAKE",
    }
    assert calls["count"] == 2


def test_retry_rejected_unless_build_failed(client):
    resp = client.post("/api/workspaces", json={"name": "No ticker", "deal_type": "buyout"})
    workspace_id = resp.json()["id"]
    retried = client.post(f"/api/workspaces/{workspace_id}/build/retry")
    assert retried.status_code == 409
    assert "not 'failed'" in retried.json()["detail"]


def test_workspace_out_exposes_build_fields(client, offline_build):
    resp = client.post("/api/workspaces", json={"ticker": "FAKE", "deal_type": "public_equity"})
    listed = client.get("/api/workspaces").json()
    created = next(w for w in listed if w["id"] == resp.json()["id"])
    assert created["build_status"] == "ready"
    assert created["build_step"] is None
    assert created["build_error"] is None
