"""Scoped API keys (G38): creation, authentication, revocation, and the scope-enforcement matrix.

These run under production auth (``AUTH_REQUIRED=true``) so the ``dlk_`` middleware branch, tenant
guard, and scope dependencies are all exercised end-to-end, matching the test_identity.py pattern.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest

from src.config import settings
from src.db.base import now_utc
from src.db.session import SessionLocal
from src.models.api_key import ApiKey
from src.schemas.identity import PrincipalContext
from src.services import api_key_service

# Reuse the fully-formed underwriting inputs so create_case returns 201 for a write-scoped key.
from test_underwriting_model import sample_assumptions  # noqa: E402
from src.schemas.underwriting_model import UnderwritingCaseCreate  # noqa: E402


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
    from src.main import _auth_rate_limiter

    _auth_rate_limiter.clear()
    response = client.post("/api/auth/register", json=_registration(label))
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_workspace(client, token: str) -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": "Programmatic access", "deal_type": "buyout"},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _case_payload() -> dict:
    return UnderwritingCaseCreate(
        case_key="base",
        label="Base case",
        assumptions=sample_assumptions(),
        created_by="api-key",
        change_note="Programmatic underwrite",
    ).model_dump(mode="json")


def _mint(client, session_token: str, organization_id: str, scopes: list[str]) -> dict:
    response = client.post(
        f"/api/organizations/{organization_id}/api-keys",
        json={"name": "CI key", "scopes": scopes},
        headers=_bearer(session_token),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _production_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    yield


def test_create_returns_plaintext_once_and_stores_only_digest(client):
    owner = _register(client, "apikey-create")
    org = owner["principal"]["organization_id"]
    created = _mint(client, owner["access_token"], org, ["read:workspaces", "read:underwriting"])

    plaintext = created["plaintext_key"]
    record = created["api_key"]
    assert plaintext.startswith("dlk_")
    assert record["key_prefix"] == plaintext[: len("dlk_") + 8]
    assert record["scopes"] == ["read:workspaces", "read:underwriting"]
    assert record["last_used_at"] is None and record["revoked_at"] is None
    # The list endpoint never re-exposes the secret.
    listing = client.get(
        f"/api/organizations/{org}/api-keys", headers=_bearer(owner["access_token"])
    )
    assert listing.status_code == 200
    assert "plaintext_key" not in listing.json()[0]
    assert all(plaintext not in str(row.values()) for row in listing.json())

    # Only the SHA-256 digest is persisted; the plaintext appears nowhere in the row.
    with SessionLocal() as session:
        stored = session.get(ApiKey, record["id"])
        assert stored is not None
        assert stored.key_digest == hashlib.sha256(plaintext.encode()).hexdigest()
        assert stored.key_digest != plaintext
        assert plaintext not in (stored.key_prefix + stored.name + str(stored.scopes))


def test_api_key_authenticates_scoped_to_org_and_rejects_revoked_and_expired(client):
    owner = _register(client, "apikey-auth")
    org = owner["principal"]["organization_id"]
    workspace_id = _create_workspace(client, owner["access_token"])
    created = _mint(client, owner["access_token"], org, ["read:underwriting"])
    key = created["plaintext_key"]

    # A live key authenticates and is scoped to its organization.
    ok = client.get(
        f"/api/workspaces/{workspace_id}/underwriting/cases", headers=_bearer(key)
    )
    assert ok.status_code == 200, ok.text
    with SessionLocal() as session:
        principal = api_key_service.authenticate_api_key(session, key)
        assert principal.organization_id == org
        assert principal.is_api_key and principal.scopes == ("read:underwriting",)
        # last_used_at was stamped on first use.
        assert session.get(ApiKey, created["api_key"]["id"]).last_used_at is not None

    # A revoked key is rejected everywhere (401).
    revoked = client.post(
        f"/api/api-keys/{created['api_key']['id']}/revoke",
        headers=_bearer(owner["access_token"]),
    )
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    assert client.get(
        f"/api/workspaces/{workspace_id}/underwriting/cases", headers=_bearer(key)
    ).status_code == 401

    # An expired key is rejected too.
    expired = _mint(client, owner["access_token"], org, ["read:underwriting"])
    with SessionLocal() as session:
        row = session.get(ApiKey, expired["api_key"]["id"])
        row.expires_at = now_utc() - timedelta(seconds=1)
        session.commit()
    assert client.get(
        f"/api/workspaces/{workspace_id}/underwriting/cases",
        headers=_bearer(expired["plaintext_key"]),
    ).status_code == 401


def test_scope_enforcement_matrix(client):
    owner = _register(client, "apikey-matrix")
    org = owner["principal"]["organization_id"]
    workspace_id = _create_workspace(client, owner["access_token"])

    read_key = _mint(client, owner["access_token"], org, ["read:underwriting"])["plaintext_key"]
    write_key = _mint(
        client, owner["access_token"], org, ["read:underwriting", "write:underwriting"]
    )["plaintext_key"]
    unrelated_key = _mint(client, owner["access_token"], org, ["read:workspaces"])["plaintext_key"]

    cases_url = f"/api/workspaces/{workspace_id}/underwriting/cases"

    def get(key: str) -> int:
        return client.get(cases_url, headers=_bearer(key)).status_code

    def post(key: str) -> int:
        return client.post(cases_url, json=_case_payload(), headers=_bearer(key)).status_code

    # (key granted scopes) x (GET read:underwriting, POST write:underwriting)
    matrix = {
        "read-only": (get(read_key), post(read_key)),
        "read+write": (get(write_key), post(write_key)),
        "unrelated-scope": (get(unrelated_key), post(unrelated_key)),
    }
    assert matrix == {
        "read-only": (200, 403),       # read allowed, write denied (insufficient scope)
        "read+write": (200, 201),      # both allowed
        "unrelated-scope": (403, 403), # neither read nor write scope granted
    }, matrix

    # A full human session (scopes is None) is unrestricted by the same gate.
    assert client.get(cases_url, headers=_bearer(owner["access_token"])).status_code == 200
    assert client.post(
        cases_url, json=_case_payload(), headers=_bearer(owner["access_token"])
    ).status_code == 201


def test_api_key_cannot_reach_other_organizations_workspace(client):
    org_a = _register(client, "apikey-tenant-a")
    org_b = _register(client, "apikey-tenant-b")
    b_workspace = _create_workspace(client, org_b["access_token"])
    a_key = _mint(
        client,
        org_a["access_token"],
        org_a["principal"]["organization_id"],
        ["read:underwriting", "write:underwriting"],
    )["plaintext_key"]

    # Tenant guard hides org B's workspace from org A's key (404, not an existence oracle).
    assert client.get(
        f"/api/workspaces/{b_workspace}/underwriting/cases", headers=_bearer(a_key)
    ).status_code == 404
    assert client.post(
        f"/api/workspaces/{b_workspace}/underwriting/cases",
        json=_case_payload(),
        headers=_bearer(a_key),
    ).status_code == 404


def test_admin_gate_and_scope_validation_on_key_administration(client):
    owner = _register(client, "apikey-admin")
    org = owner["principal"]["organization_id"]
    # Unknown scopes are rejected at creation.
    bad = client.post(
        f"/api/organizations/{org}/api-keys",
        json={"name": "bad", "scopes": ["read:everything"]},
        headers=_bearer(owner["access_token"]),
    )
    assert bad.status_code == 400, bad.text

    # A non-admin member cannot mint keys for the org.
    member = _register(client, "apikey-outsider")
    member_key_attempt = client.post(
        f"/api/organizations/{org}/api-keys",
        json={"name": "sneaky", "scopes": ["read:underwriting"]},
        headers=_bearer(member["access_token"]),
    )
    assert member_key_attempt.status_code == 403, member_key_attempt.text


def test_require_scope_dependency_is_unrestricted_for_non_key_principals():
    from fastapi import HTTPException

    from src.routers.deps import require_scope

    dependency = require_scope("write:underwriting")

    class _Req:
        class state:  # noqa: N801 - minimal stand-in for Starlette request.state
            principal = None

    # No principal (auth-off/dev) -> no-op.
    assert dependency(_Req) is None

    # Human session principal (scopes is None) -> unrestricted.
    _Req.state.principal = PrincipalContext(
        user_id="u", session_id="s", email="e@x.test", display_name="U",
        organization_id="o", membership_id="m", role="member",
    )
    assert dependency(_Req) is None

    # API-key principal missing the scope -> 403.
    _Req.state.principal = PrincipalContext(
        user_id="u", session_id="s", email="e@x.test", display_name="U",
        organization_id="o", membership_id="m", role="member", scopes=("read:underwriting",),
    )
    with pytest.raises(HTTPException) as excinfo:
        dependency(_Req)
    assert excinfo.value.status_code == 403
