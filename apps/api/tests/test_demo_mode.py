"""Public-demo posture: guest sessions, per-IP build throttling, and retention cleanup."""
from __future__ import annotations

from datetime import timedelta

import pytest

from src.config import settings
from src.db.base import now_utc
from src.db.session import SessionLocal
from src.models import Organization, User, Workspace
from src.services import demo_service


@pytest.fixture()
def demo_mode(monkeypatch):
    from src.main import _demo_build_rate_limiter

    monkeypatch.setattr(settings, "demo_mode", True)
    _demo_build_rate_limiter.clear()
    yield
    _demo_build_rate_limiter.clear()


def test_demo_session_disabled_by_default(client):
    assert settings.demo_mode is False
    resp = client.post("/api/auth/demo")
    assert resp.status_code == 403


def test_guest_session_is_a_real_scoped_identity(client, demo_mode):
    resp = client.post("/api/auth/demo")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    token = body["access_token"]
    assert token.startswith("dls_")
    assert body["principal"]["role"] == "member"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    with SessionLocal() as session:
        organization = session.get(Organization, body["principal"]["organization_id"])
        assert organization is not None and organization.slug == demo_service.DEMO_ORG_SLUG

    # Two guests are distinct identities in the same sandbox tenant.
    second = client.post("/api/auth/demo").json()
    assert second["principal"]["user_id"] != body["principal"]["user_id"]
    assert second["principal"]["organization_id"] == body["principal"]["organization_id"]


def test_demo_mode_throttles_build_endpoints_per_ip(client, demo_mode, monkeypatch):
    monkeypatch.setattr(settings, "demo_builds_per_hour", 2)
    for _ in range(2):
        resp = client.post(
            "/api/workspaces", json={"name": "throttle probe", "deal_type": "buyout"}
        )
        assert resp.status_code == 201, resp.text
    throttled = client.post(
        "/api/workspaces", json={"name": "one too many", "deal_type": "buyout"}
    )
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers
    # Read endpoints stay fully available while throttled.
    assert client.get("/api/workspaces").status_code == 200


def test_purge_removes_only_expired_demo_data(client, demo_mode):
    guest = client.post("/api/auth/demo").json()
    token = guest["access_token"]
    ws = client.post(
        "/api/workspaces",
        json={"name": "Expired demo workspace", "deal_type": "buyout"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    keeper = client.post(
        "/api/workspaces", json={"name": "Unowned keeper", "deal_type": "buyout"}
    ).json()

    stale = now_utc().replace(tzinfo=None) - timedelta(
        hours=settings.demo_retention_hours + 1
    )
    with SessionLocal() as session:
        session.get(Workspace, ws["id"]).created_at = stale
        session.get(User, guest["principal"]["user_id"]).created_at = stale
        session.commit()

    with SessionLocal() as session:
        counts = demo_service.purge_expired_demo_data(session)
    assert counts["workspaces"] >= 1
    assert counts["guest_users"] >= 1

    with SessionLocal() as session:
        assert session.get(Workspace, ws["id"]) is None
        assert session.get(User, guest["principal"]["user_id"]) is None
        # Data outside the demo organization is never scanned.
        assert session.get(Workspace, keeper["id"]) is not None
