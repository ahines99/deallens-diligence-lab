"""End-to-end coverage for authenticated identity and tenant-scoped workspaces."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base, now_utc
from src.db.session import SessionLocal
from src.models.identity import AuthSession, User
from src.models.target import Target
from src.models.workspace import Workspace
from src.schemas.identity import RegistrationCreate
from src.services import identity_service


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
    response = client.post("/api/auth/register", json=_registration(label))
    assert response.status_code == 201, response.text
    return response.json()


def _authorization(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


def test_registration_disabled_still_allows_exactly_one_bootstrap_owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_allow_registration", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = identity_service.register(
            session, RegistrationCreate.model_validate(_registration("bootstrap"))
        )
        assert first.principal.role == "owner"
        with pytest.raises(identity_service.IdentityForbidden, match="disabled"):
            identity_service.register(
                session, RegistrationCreate.model_validate(_registration("closed"))
            )
    engine.dispose()


def test_sessions_memberships_and_workspace_tenants_are_server_derived(client):
    alpha = _register(client, "alpha")
    beta = _register(client, "beta")
    alpha_headers = _authorization(
        alpha["access_token"],
        **{"X-Organization-ID": beta["principal"]["organization_id"]},
    )
    beta_headers = _authorization(beta["access_token"])

    alpha_workspace = client.post(
        "/api/workspaces",
        json={"name": "Alpha only", "deal_type": "buyout"},
        headers=alpha_headers,
    )
    assert alpha_workspace.status_code == 201, alpha_workspace.text
    alpha_workspace = alpha_workspace.json()
    assert alpha_workspace["organization_id"] == alpha["principal"]["organization_id"]

    beta_workspace = client.post(
        "/api/workspaces",
        json={"name": "Beta only", "deal_type": "buyout"},
        headers=beta_headers,
    )
    assert beta_workspace.status_code == 201, beta_workspace.text
    beta_workspace = beta_workspace.json()

    alpha_list = client.get("/api/workspaces", headers=alpha_headers)
    assert alpha_list.status_code == 200
    assert {item["id"] for item in alpha_list.json()} == {alpha_workspace["id"]}
    assert client.get(
        f"/api/workspaces/{beta_workspace['id']}", headers=alpha_headers
    ).status_code == 404

    added = client.post(
        f"/api/organizations/{alpha['principal']['organization_id']}/members",
        json={"email": beta["principal"]["email"], "role": "viewer"},
        headers=_authorization(alpha["access_token"]),
    )
    assert added.status_code == 201, added.text

    switched = client.post(
        "/api/auth/switch-organization",
        json={"organization_id": alpha["principal"]["organization_id"]},
        headers=beta_headers,
    )
    assert switched.status_code == 200, switched.text
    viewer_token = switched.json()["access_token"]
    viewer_headers = _authorization(viewer_token)
    assert client.get(
        f"/api/workspaces/{alpha_workspace['id']}", headers=viewer_headers
    ).status_code == 200
    assert client.post(
        "/api/workspaces",
        json={"name": "Viewer must not write", "deal_type": "buyout"},
        headers=viewer_headers,
    ).status_code == 403
    assert client.patch(
        f"/api/workspaces/{alpha_workspace['id']}/governance",
        json={"data_classification": "restricted"},
        headers=viewer_headers,
    ).status_code == 403

    governed = client.patch(
        f"/api/workspaces/{alpha_workspace['id']}/governance",
        json={"data_classification": "confidential", "external_llm_allowed": True},
        headers=_authorization(alpha["access_token"]),
    )
    assert governed.status_code == 200, governed.text
    assert governed.json()["data_classification"] == "confidential"
    assert governed.json()["external_llm_allowed"] is True
    rejected_policy = client.patch(
        f"/api/workspaces/{alpha_workspace['id']}/governance",
        json={"data_classification": "restricted"},
        headers=_authorization(alpha["access_token"]),
    )
    assert rejected_policy.status_code == 400
    restricted = client.patch(
        f"/api/workspaces/{alpha_workspace['id']}/governance",
        json={"data_classification": "restricted", "external_llm_allowed": False},
        headers=_authorization(alpha["access_token"]),
    )
    assert restricted.status_code == 200
    assert restricted.json()["data_classification"] == "restricted"

    # Switching and logout both revoke the prior opaque token immediately.
    assert client.get("/api/auth/me", headers=beta_headers).status_code == 401
    assert client.post("/api/auth/logout", headers=viewer_headers).json() == {"revoked": True}
    assert client.get("/api/auth/me", headers=viewer_headers).status_code == 401


def test_owner_admin_member_viewer_authorization_matrix(client):
    owner = _register(client, "matrix-owner")
    admin_user = _register(client, "matrix-admin")
    member_user = _register(client, "matrix-member")
    viewer_user = _register(client, "matrix-viewer")
    organization_id = owner["principal"]["organization_id"]
    owner_headers = _authorization(owner["access_token"])

    memberships = {}
    for account, role in (
        (admin_user, "admin"),
        (member_user, "member"),
        (viewer_user, "viewer"),
    ):
        added = client.post(
            f"/api/organizations/{organization_id}/members",
            json={"email": account["principal"]["email"], "role": role},
            headers=owner_headers,
        )
        assert added.status_code == 201, added.text
        memberships[role] = added.json()

    def switch(account: dict) -> dict[str, str]:
        response = client.post(
            "/api/auth/switch-organization",
            json={"organization_id": organization_id},
            headers=_authorization(account["access_token"]),
        )
        assert response.status_code == 200, response.text
        return _authorization(response.json()["access_token"])

    admin_headers = switch(admin_user)
    member_headers = switch(member_user)
    viewer_headers = switch(viewer_user)
    workspace = client.post(
        "/api/workspaces",
        json={"name": "Role matrix", "deal_type": "buyout"},
        headers=member_headers,
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["id"]

    assert client.patch(
        f"/api/workspaces/{workspace_id}/governance",
        json={"data_classification": "confidential"},
        headers=member_headers,
    ).status_code == 403
    assert client.patch(
        f"/api/workspaces/{workspace_id}/governance",
        json={"data_classification": "confidential"},
        headers=admin_headers,
    ).status_code == 200
    assert client.patch(
        f"/api/memberships/{memberships['member']['id']}",
        json={"role": "member"},
        headers=admin_headers,
    ).status_code == 200
    assert client.get(
        f"/api/workspaces/{workspace_id}", headers=viewer_headers
    ).status_code == 200
    assert client.post(
        "/api/workspaces",
        json={"name": "Viewer write", "deal_type": "buyout"},
        headers=viewer_headers,
    ).status_code == 403
    assert client.patch(
        f"/api/workspaces/{workspace_id}/governance",
        json={"data_classification": "internal"},
        headers=owner_headers,
    ).status_code == 200


def test_auth_required_keeps_health_and_login_public(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/workspaces").status_code == 401

    account = _register(client, "required")
    response = client.post(
        "/api/workspaces",
        json={"name": "Authenticated", "deal_type": "buyout"},
        headers=_authorization(account["access_token"]),
    )
    assert response.status_code == 201, response.text


def test_public_auth_hash_work_is_rate_limited(client, monkeypatch):
    from src.main import _auth_rate_limiter

    _auth_rate_limiter.clear()
    monkeypatch.setattr(settings, "auth_rate_limit_attempts", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 60)
    payload = {"email": "rate-limit-unknown@example.test", "password": "not the password"}
    try:
        assert client.post("/api/auth/login", json=payload).status_code == 401
        assert client.post("/api/auth/login", json=payload).status_code == 401
        limited = client.post("/api/auth/login", json=payload)
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
    finally:
        _auth_rate_limiter.clear()


def test_passwords_are_salted_and_sessions_expire_server_side(client):
    registration = _registration("expiry")
    response = client.post("/api/auth/register", json=registration)
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.email_normalized == registration["email"])
        )
        assert user is not None
        assert registration["password"] not in user.password_hash
        assert user.password_hash.startswith("pbkdf2-sha256$600000$")
        auth_session = session.get(AuthSession, response.json()["principal"]["session_id"])
        assert auth_session is not None
        assert auth_session.token_digest != token
        auth_session.expires_at = now_utc() - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/auth/me", headers=_authorization(token)).status_code == 401


def test_login_lockout_and_server_owned_target_provenance(client, monkeypatch):
    registration = _registration("lockout")
    account = client.post("/api/auth/register", json=registration)
    assert account.status_code == 201
    monkeypatch.setattr(settings, "auth_max_failed_logins", 2)

    for _ in range(2):
        denied = client.post(
            "/api/auth/login",
            json={"email": registration["email"], "password": "definitely incorrect"},
        )
        assert denied.status_code == 401
    locked = client.post(
        "/api/auth/login",
        json={"email": registration["email"], "password": registration["password"]},
    )
    assert locked.status_code == 429

    headers = _authorization(account.json()["access_token"])
    workspace = client.post(
        "/api/workspaces",
        json={"name": "Provenance", "deal_type": "buyout"},
        headers=headers,
    ).json()
    rejected = client.post(
        f"/api/workspaces/{workspace['id']}/target",
        json={
            "name": "Untrusted Target",
            "target_type": "private_company",
            "data_source": "SEC EDGAR (XBRL)",
            "financials": {"pretend": "source metadata"},
        },
        headers=headers,
    )
    assert rejected.status_code == 422
    accepted = client.post(
        f"/api/workspaces/{workspace['id']}/target",
        json={"name": "Untrusted Target", "target_type": "private_company", "revenue": 10},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data_source"] == "User-submitted target profile (unverified)"

    source = client.post(
        f"/api/workspaces/{workspace['id']}/underwriting/sources",
        json={
            "source_kind": "filing",
            "source_type": "SEC EDGAR XBRL",
            "source_name": "Client-asserted filing",
            "content_hash": "c" * 64,
            "status": "ready",
            "created_by": "sec-system",
        },
        headers={**headers, "X-Actor-ID": "spoofed-actor"},
    )
    assert source.status_code == 201, source.text
    source_body = source.json()
    assert source_body["source_kind"] == "user_input"
    assert source_body["source_type"] == "user_registered_reference"
    assert source_body["status"] == "partial"
    assert source_body["created_by"] == account.json()["principal"]["user_id"]
    assert source_body["source_metadata"]["verification_status"] == "unverified"
    assert source_body["source_metadata"]["declared"]["source_kind"] == "filing"

    rejected_comp = client.post(
        f"/api/workspaces/{workspace['id']}/comps",
        json={
            "comps": [
                {
                    "ticker": "FAKE",
                    "company_name": "Unverified Peer",
                    "revenue": 999,
                    "data_source": "SEC EDGAR (XBRL)",
                    "is_illustrative": False,
                }
            ]
        },
        headers=headers,
    )
    assert rejected_comp.status_code == 422
    accepted_comp = client.post(
        f"/api/workspaces/{workspace['id']}/comps",
        json={
            "comps": [
                {
                    "ticker": "MANUAL",
                    "company_name": "Unverified Peer",
                    "revenue": 999,
                }
            ]
        },
        headers=headers,
    )
    assert accepted_comp.status_code == 200, accepted_comp.text
    comp_body = accepted_comp.json()[0]
    assert comp_body["data_source"] == "User-submitted comparable profile (unverified)"
    assert comp_body["is_illustrative"] is True


def test_sqlite_foreign_keys_are_enforced(client):
    # The client fixture starts the schema; this insert must fail instead of creating an orphan.
    with SessionLocal() as session:
        session.add(
            Target(
                workspace_id="0" * 32,
                name="Orphan",
                target_type="private_company",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            Workspace(
                name="Orphan target link",
                deal_type="buyout",
                target_id="0" * 32,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_sec_ingest_is_tenant_scoped_under_real_auth(client, monkeypatch):
    """H1 end-to-end under production auth: the body-addressed /api/sec/ingest endpoint must
    reject a member trying to ingest into another org's workspace (principal-based guard,
    the branch conftest's auth-off default never exercises)."""
    monkeypatch.setattr(settings, "auth_required", True)
    from src.main import _auth_rate_limiter

    _auth_rate_limiter.clear()
    try:
        owner = _register(client, "ingest-owner")
        other = _register(client, "ingest-attacker")
        victim_ws = client.post(
            "/api/workspaces",
            json={"name": "Victim workspace", "deal_type": "buyout"},
            headers=_authorization(owner["access_token"]),
        ).json()

        # The attacker (a different org) cannot ingest into the victim's workspace.
        attack = client.post(
            "/api/sec/ingest",
            json={"workspace_id": victim_ws["id"], "ticker": "AAPL"},
            headers=_authorization(other["access_token"]),
        )
        assert attack.status_code == 404, attack.text  # 404, not an existence oracle
    finally:
        _auth_rate_limiter.clear()
