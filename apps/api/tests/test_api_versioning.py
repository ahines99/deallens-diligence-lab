"""Compatibility coverage for the stable `/api/v1` contract alias."""
from __future__ import annotations


def test_v1_alias_reaches_existing_routes_and_declares_version(client):
    legacy = client.get("/api/health")
    versioned = client.get("/api/v1/health")
    assert legacy.status_code == versioned.status_code == 200
    assert legacy.json() == versioned.json()
    assert legacy.json()["database_status"] == "ready"
    assert legacy.headers["X-Content-Type-Options"] == "nosniff"
    assert legacy.headers["X-Frame-Options"] == "DENY"
    assert versioned.headers["X-DealLens-API-Version"] == "1"


def test_v1_alias_preserves_path_parameters(client):
    workspace = client.post(
        "/api/v1/workspaces", json={"name": "Versioned API", "deal_type": "buyout"}
    )
    assert workspace.status_code == 201, workspace.text
    response = client.get(f"/api/v1/workspaces/{workspace.json()['id']}")
    assert response.status_code == 200
    assert response.json()["workspace"]["name"] == "Versioned API"
    assert response.headers["X-DealLens-API-Version"] == "1"
