"""Offline tenant-isolation coverage for workspace routes linked to deals."""
from __future__ import annotations


def test_linked_workspace_is_hidden_from_other_organization(client):
    workspace = client.post(
        "/api/workspaces", json={"name": "Tenant scoped", "deal_type": "buyout"}
    ).json()
    organization_response = client.post(
        "/api/organizations",
        json={"name": "Tenant Guard Organization", "slug": "tenant-guard-organization"},
        headers={"X-Actor-ID": "admin"},
    )
    assert organization_response.status_code == 201, organization_response.text
    organization = organization_response.json()
    headers = {"X-Actor-ID": "admin", "X-Organization-ID": organization["id"]}
    fund_response = client.post(
        f"/api/organizations/{organization['id']}/funds",
        json={"name": "Fund I"},
        headers=headers,
    )
    assert fund_response.status_code == 201, fund_response.text
    deal_response = client.post(
        f"/api/funds/{fund_response.json()['id']}/deals",
        json={
            "code": "TENANT-1",
            "name": "Tenant Deal",
            "target_company": "Tenant Target",
            "workspace_id": workspace["id"],
        },
        headers=headers,
    )
    assert deal_response.status_code == 201, deal_response.text

    assert client.get(
        f"/api/workspaces/{workspace['id']}",
        headers={"X-Actor-ID": "outsider", "X-Organization-ID": "f" * 32},
    ).status_code == 404
    assert client.get(f"/api/workspaces/{workspace['id']}", headers=headers).status_code == 200
