"""Optional OIDC SSO (G48): config-gated endpoints, code-exchange callback, and role mapping.

A live IdP is unavailable in CI, so the token exchange is monkeypatched to return a canned
``id_token`` (an unsigned JWT the test crafts). This exercises the real claim-parsing, basic
iss/aud/exp validation, find-or-create linking, and role-map resolution. Signature verification is
intentionally out of scope in the service (documented there) and therefore not asserted here.
"""
from __future__ import annotations

import base64
import json
import time
import uuid

from src.config import settings
from src.services import oidc_service

_ISSUER = "https://idp.example.test"
_CLIENT_ID = "deallens-client"
_REDIRECT = "https://app.example.test/api/auth/oidc/callback"


def _b64url(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _id_token(claims: dict) -> str:
    """An UNSIGNED JWT (header.payload.signature) carrying the given claims."""
    header = _b64url({"alg": "none", "typ": "JWT"})
    return f"{header}.{_b64url(claims)}.notarealsignature"


def _claims(email: str, roles, *, verified: bool = True) -> dict:
    return {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "email": email,
        "email_verified": verified,
        "name": "SSO User",
        "roles": roles,
    }


def _registration(label: str, slug: str) -> dict[str, str]:
    return {
        "email": f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        "display_name": f"{label.title()} Bootstrap",
        "password": "correct horse portfolio battery",
        "organization_name": f"{label.title()} SSO Corp",
        "organization_slug": slug,
    }


def _enable_oidc(monkeypatch, *, org_slug: str, role_map: dict) -> None:
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", _ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", _CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_client_secret", "shh")
    monkeypatch.setattr(settings, "oidc_redirect_uri", _REDIRECT)
    monkeypatch.setattr(settings, "oidc_role_claim", "roles")
    monkeypatch.setattr(settings, "oidc_role_map", json.dumps(role_map))
    monkeypatch.setattr(settings, "oidc_organization_slug", org_slug)


def _provision_org(client, slug: str) -> str:
    from src.main import _auth_rate_limiter

    _auth_rate_limiter.clear()
    response = client.post("/api/auth/register", json=_registration("sso", slug))
    assert response.status_code == 201, response.text
    return response.json()["principal"]["organization_id"]


def _mock_exchange(monkeypatch, claims: dict) -> None:
    monkeypatch.setattr(
        oidc_service, "_exchange_code", lambda code: {"id_token": _id_token(claims)}
    )


# --- config gate -----------------------------------------------------------------------------


def test_login_url_when_enabled(monkeypatch, client):
    _enable_oidc(monkeypatch, org_slug="sso-enabled", role_map={})
    response = client.get("/api/auth/oidc/login")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authorize_url"].startswith(_ISSUER)
    assert f"client_id={_CLIENT_ID}" in body["authorize_url"]
    assert "response_type=code" in body["authorize_url"]
    assert body["state"] and f"state={body['state']}" in body["authorize_url"]


def test_endpoints_404_when_disabled(monkeypatch, client):
    monkeypatch.setattr(settings, "oidc_enabled", False)
    assert client.get("/api/auth/oidc/login").status_code == 404
    assert client.get("/api/auth/oidc/callback", params={"code": "x"}).status_code == 404


# --- callback + role mapping -----------------------------------------------------------------


def test_callback_creates_user_and_session_with_mapped_role(monkeypatch, client):
    slug = f"sso-map-{uuid.uuid4().hex[:8]}"
    org_id = _provision_org(client, slug)
    _enable_oidc(monkeypatch, org_slug=slug, role_map={"pe-admins": "admin", "analysts": "member"})
    email = f"admin-{uuid.uuid4().hex[:8]}@sso.example.test"
    _mock_exchange(monkeypatch, _claims(email, ["pe-admins"]))

    response = client.get(
        "/api/auth/oidc/callback", params={"code": "auth-code", "state": "abc"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["principal"]["role"] == "admin"
    assert body["principal"]["organization_id"] == org_id
    assert body["principal"]["email"] == email

    # The issued session is a real, usable DealLens session.
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["principal"]["role"] == "admin"


def test_unmapped_role_falls_back_to_viewer_least_privilege(monkeypatch, client):
    slug = f"sso-unmapped-{uuid.uuid4().hex[:8]}"
    _provision_org(client, slug)
    _enable_oidc(monkeypatch, org_slug=slug, role_map={"pe-admins": "admin"})
    email = f"stranger-{uuid.uuid4().hex[:8]}@sso.example.test"
    _mock_exchange(monkeypatch, _claims(email, ["some-unrecognized-group"]))

    response = client.get("/api/auth/oidc/callback", params={"code": "auth-code"})
    assert response.status_code == 200, response.text
    assert response.json()["principal"]["role"] == "viewer"


def test_missing_role_claim_falls_back_to_viewer(monkeypatch, client):
    slug = f"sso-norole-{uuid.uuid4().hex[:8]}"
    _provision_org(client, slug)
    _enable_oidc(monkeypatch, org_slug=slug, role_map={"pe-admins": "admin"})
    claims = _claims(f"norole-{uuid.uuid4().hex[:8]}@sso.example.test", ["pe-admins"])
    claims.pop("roles")
    _mock_exchange(monkeypatch, claims)

    response = client.get("/api/auth/oidc/callback", params={"code": "auth-code"})
    assert response.status_code == 200, response.text
    assert response.json()["principal"]["role"] == "viewer"


def test_existing_user_is_linked_and_membership_role_is_stable(monkeypatch, client):
    slug = f"sso-link-{uuid.uuid4().hex[:8]}"
    _provision_org(client, slug)
    _enable_oidc(monkeypatch, org_slug=slug, role_map={"pe-admins": "admin", "analysts": "member"})
    email = f"repeat-{uuid.uuid4().hex[:8]}@sso.example.test"

    _mock_exchange(monkeypatch, _claims(email, ["pe-admins"]))
    first = client.get("/api/auth/oidc/callback", params={"code": "code-1"})
    assert first.status_code == 200, first.text
    first_principal = first.json()["principal"]
    assert first_principal["role"] == "admin"

    # A second login for the SAME email links the existing user and does NOT re-grant/downgrade
    # the already-provisioned membership even though the IdP now claims a lower role.
    _mock_exchange(monkeypatch, _claims(email, ["analysts"]))
    second = client.get("/api/auth/oidc/callback", params={"code": "code-2"})
    assert second.status_code == 200, second.text
    second_principal = second.json()["principal"]
    assert second_principal["user_id"] == first_principal["user_id"]
    assert second_principal["membership_id"] == first_principal["membership_id"]
    assert second_principal["role"] == "admin"


def test_unverified_email_is_rejected(monkeypatch, client):
    slug = f"sso-unverified-{uuid.uuid4().hex[:8]}"
    _provision_org(client, slug)
    _enable_oidc(monkeypatch, org_slug=slug, role_map={"pe-admins": "admin"})
    claims = _claims(f"bad-{uuid.uuid4().hex[:8]}@sso.example.test", ["pe-admins"], verified=False)
    _mock_exchange(monkeypatch, claims)

    response = client.get("/api/auth/oidc/callback", params={"code": "auth-code"})
    assert response.status_code == 401, response.text


def test_expired_id_token_is_rejected(monkeypatch, client):
    slug = f"sso-expired-{uuid.uuid4().hex[:8]}"
    _provision_org(client, slug)
    _enable_oidc(monkeypatch, org_slug=slug, role_map={"pe-admins": "admin"})
    claims = _claims(f"exp-{uuid.uuid4().hex[:8]}@sso.example.test", ["pe-admins"])
    claims["exp"] = int(time.time()) - 60
    _mock_exchange(monkeypatch, claims)

    response = client.get("/api/auth/oidc/callback", params={"code": "auth-code"})
    assert response.status_code == 401, response.text
