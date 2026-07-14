"""The bundled example private deal loads through the real governed pipeline, offline."""
from __future__ import annotations

HEADERS = {"X-Actor-ID": "visitor@example.test", "X-Actor-Name": "Visitor"}


def test_example_deal_loads_end_to_end(client):
    resp = client.post("/api/examples/private-deal", headers=HEADERS)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    workspace_id = body["workspace_id"]
    deal_id = body["deal_id"]

    # The financial import landed clean: every row mapped, balance sheet balanced.
    assert body["import_status"] == "ready"
    assert body["open_exceptions"] == 0

    # Private target, clearly labeled fictional, owned by the demo organization.
    target = client.get(f"/api/workspaces/{workspace_id}/target").json()
    assert "Fictional" in target["name"]
    assert target["ticker"] is None

    # Facts came through the real import pipeline with provenance.
    facts = client.get(f"/api/workspaces/{workspace_id}/underwriting/financial-facts").json()
    assert any(fact["canonical_account"] == "ebitda" for fact in facts)

    # QoE adjustments are PROPOSED — approval is left for the visitor (four-eyes).
    bridge = client.get(f"/api/workspaces/{workspace_id}/underwriting/qoe-bridge").json()
    adjustments = client.get(
        f"/api/workspaces/{workspace_id}/underwriting/qoe-adjustments"
    ).json()
    assert len(adjustments) == 3
    assert all(item["status"] == "proposed" for item in adjustments)
    assert bridge["management_ebitda"] == bridge["reported_ebitda"]

    # Data-room documents ingested with automatic chunking, so cited Q&A works now.
    docs = client.get(
        f"/api/deals/{deal_id}/intelligence/documents", headers=HEADERS
    ).json()
    assert len(docs) == 3
    qa = client.post(
        f"/api/deals/{deal_id}/intelligence/qa",
        json={"question": "What was gross revenue churn in fiscal 2025?"},
        headers=HEADERS,
    )
    assert qa.status_code in (200, 201), qa.text
    assert qa.json()["citations"], "cited Q&A should ground its answer in the data room"


def test_repeat_loads_create_distinct_deals(client):
    first = client.post("/api/examples/private-deal", headers=HEADERS).json()
    second = client.post("/api/examples/private-deal", headers=HEADERS).json()
    assert first["deal_id"] != second["deal_id"]
    assert first["deal_code"] != second["deal_code"]
    assert first["workspace_id"] != second["workspace_id"]


def test_templates_are_listable_and_downloadable(client):
    listed = client.get("/api/examples/templates").json()
    names = {item["name"] for item in listed}
    assert "management_financials.csv" in names

    download = client.get("/api/examples/templates/management_financials.csv")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert b"canonical_account" in download.content

    missing = client.get("/api/examples/templates/../secrets.txt")
    assert missing.status_code == 404
