"""Per-organization quotas and rate limits (G39): boundary, isolation, and reset coverage.

These run under production auth (``AUTH_REQUIRED=true``) so a real ``request.state.principal`` with
an ``organization_id`` is resolved and the quota middleware actually attributes usage per tenant.
The process-global limiter is cleared in a fixture (mirroring test_demo_mode.py's demo limiter) so
these tests never leak state into — or trip on — the rest of the suite.
"""
from __future__ import annotations

import uuid

import pytest

from src.config import settings
from src.main import _auth_rate_limiter, _org_quota_limiter


@pytest.fixture(autouse=True)
def _quota_isolation(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    _org_quota_limiter.clear()
    _auth_rate_limiter.clear()
    yield
    _org_quota_limiter.clear()
    _auth_rate_limiter.clear()


def _registration(label: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:10]
    return {
        "email": f"{label}-{suffix}@example.test",
        "display_name": f"{label.title()} Analyst",
        "password": "correct horse portfolio battery",
        "organization_name": f"{label.title()} Capital {suffix}",
        "organization_slug": f"{label}-capital-{suffix}",
    }


def _register(client, label: str) -> dict:
    _auth_rate_limiter.clear()
    response = client.post("/api/auth/register", json=_registration(label))
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _build(client, token: str) -> int:
    return client.post(
        "/api/workspaces",
        json={"name": "quota build", "deal_type": "buyout"},
        headers=_bearer(token),
    ).status_code


def test_request_quota_boundary_is_per_tenant(client, monkeypatch):
    """(a) The N+1th request from an org is 429 with Retry-After; a different org is unaffected."""
    monkeypatch.setattr(settings, "org_request_quota_per_minute", 3)
    monkeypatch.setattr(settings, "org_build_quota_per_hour", 0)  # isolate the request bucket
    _org_quota_limiter.clear()

    org_a = _register(client, "reqquota-a")
    org_b = _register(client, "reqquota-b")
    a_headers = _bearer(org_a["access_token"])

    # Exactly the limit worth of authenticated requests are allowed through.
    for _ in range(3):
        assert client.get("/api/auth/me", headers=a_headers).status_code == 200

    limited = client.get("/api/auth/me", headers=a_headers)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1

    # Per-tenant isolation: a different org still has its full allowance.
    assert client.get("/api/auth/me", headers=_bearer(org_b["access_token"])).status_code == 200


def test_build_quota_boundary(client, monkeypatch):
    """(b) The N+1th build for an org is 429; a different org can still build."""
    monkeypatch.setattr(settings, "org_build_quota_per_hour", 2)
    monkeypatch.setattr(settings, "org_request_quota_per_minute", 0)  # unlimited requests
    _org_quota_limiter.clear()

    owner = _register(client, "buildquota")
    token = owner["access_token"]
    for _ in range(2):
        assert _build(client, token) == 201

    throttled = client.post(
        "/api/workspaces",
        json={"name": "one too many", "deal_type": "buyout"},
        headers=_bearer(token),
    )
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers

    # Reads remain fully available even while builds are throttled.
    assert client.get("/api/workspaces", headers=_bearer(token)).status_code == 200
    # A separate tenant is unaffected by another org's exhausted build quota.
    other = _register(client, "buildquota-other")
    assert _build(client, other["access_token"]) == 201


def test_quota_usage_endpoint_reports_and_is_org_scoped(client, monkeypatch):
    """(c) The quota-usage endpoint reports used/remaining accurately and is tenant-scoped."""
    monkeypatch.setattr(settings, "org_build_quota_per_hour", 5)
    monkeypatch.setattr(settings, "org_request_quota_per_minute", 0)  # keep 'requests' deterministic
    _org_quota_limiter.clear()

    owner = _register(client, "usage")
    org = owner["principal"]["organization_id"]
    token = owner["access_token"]
    for _ in range(3):
        assert _build(client, token) == 201

    usage = client.get(f"/api/organizations/{org}/quota-usage", headers=_bearer(token))
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["organization_id"] == org
    buckets = {bucket["name"]: bucket for bucket in body["buckets"]}

    assert buckets["builds"]["used"] == 3
    assert buckets["builds"]["limit"] == 5
    assert buckets["builds"]["remaining"] == 2
    assert buckets["builds"]["window_seconds"] == 3600
    # Unlimited bucket reports remaining as null.
    assert buckets["requests"]["limit"] == 0
    assert buckets["requests"]["remaining"] is None
    assert buckets["requests"]["used"] == 0

    # Cross-tenant read is a 404 (never an existence oracle).
    other = _register(client, "usage-other")
    assert client.get(
        f"/api/organizations/{org}/quota-usage",
        headers=_bearer(other["access_token"]),
    ).status_code == 404


def test_unlimited_quota_never_throttles(client, monkeypatch):
    """(d) A limit of 0 disables throttling for both buckets."""
    monkeypatch.setattr(settings, "org_request_quota_per_minute", 0)
    monkeypatch.setattr(settings, "org_build_quota_per_hour", 0)
    _org_quota_limiter.clear()

    owner = _register(client, "unlimited")
    token = owner["access_token"]
    for _ in range(12):
        assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 200
    for _ in range(8):
        assert _build(client, token) == 201


def test_clearing_the_limiter_resets_usage(client, monkeypatch):
    """(e) clear() releases an exhausted quota so requests succeed again."""
    monkeypatch.setattr(settings, "org_request_quota_per_minute", 1)
    monkeypatch.setattr(settings, "org_build_quota_per_hour", 0)
    _org_quota_limiter.clear()

    owner = _register(client, "reset")
    headers = _bearer(owner["access_token"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 429

    _org_quota_limiter.clear()
    assert client.get("/api/auth/me", headers=headers).status_code == 200
