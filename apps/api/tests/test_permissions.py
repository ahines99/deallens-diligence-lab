"""Fine-grained permission matrix (G49): catalog, deny-by-default resolution, and enforcement.

The pure-resolution tests assert the exhaustive role→capability table and per-membership
grant/revoke overrides directly; the endpoint test runs under production auth so the
``require_capability`` dependency, session capability resolution, and admin-gated grant API are
exercised end-to-end (mirroring the test_api_keys pattern).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models.identity import OrganizationMembership, User
from src.models.permission import MembershipPermission
from src.permissions import (
    ALL_CAPABILITIES,
    CAPABILITIES,
    ROLE_DEFAULTS,
    role_default_capabilities,
)
from src.schemas.identity import PrincipalContext
from src.services.identity_service import effective_capabilities

from test_underwriting_model import sample_assumptions  # noqa: E402
from src.schemas.underwriting_model import UnderwritingCaseCreate  # noqa: E402


# --- pure catalog + role-default table ------------------------------------------------------


def test_capability_catalog_is_the_authoritative_set():
    assert ALL_CAPABILITIES == frozenset(CAPABILITIES)
    # Every capability referenced by a role default is a member of the catalog (no typos/orphans).
    for role, caps in ROLE_DEFAULTS.items():
        assert caps <= ALL_CAPABILITIES, role


def test_role_defaults_reproduce_the_coarse_behavior_exhaustively():
    """Exhaustive role×capability table: allowed/denied for every (role, capability) pair."""
    expected = {
        "viewer": {"workspace:read", "underwriting:read"},
        "member": {"workspace:read", "underwriting:read", "workspace:write", "underwriting:write"},
        "admin": {
            "workspace:read",
            "underwriting:read",
            "workspace:write",
            "underwriting:write",
            "underwriting:approve",
            "ic:decide",
            "governance:manage",
            "member:manage",
            "apikey:manage",
        },
        "owner": set(ALL_CAPABILITIES),
    }
    table = {}
    for role in ("viewer", "member", "admin", "owner"):
        allowed = role_default_capabilities(role)
        for capability in ALL_CAPABILITIES:
            table[(role, capability)] = capability in allowed
        # The role's allowed set is exactly the expected set — nothing extra, nothing missing.
        assert set(allowed) == expected[role], role

    # Coarse invariants: viewer read-only; only owner holds the owner-exclusive capability;
    # decide/approve are withheld from members by default (they must be granted).
    assert table[("viewer", "workspace:write")] is False
    assert table[("viewer", "workspace:read")] is True
    assert table[("member", "underwriting:approve")] is False
    assert table[("member", "ic:decide")] is False
    assert table[("admin", "organization:manage")] is False
    assert table[("owner", "organization:manage")] is True


def test_unknown_role_is_denied_everything():
    assert role_default_capabilities("robot") == frozenset()


# --- per-membership grant / revoke resolution (deny-by-default) ------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _membership(session: Session, role: str) -> OrganizationMembership:
    user = User(
        email=f"{uuid.uuid4().hex}@example.test",
        email_normalized=f"{uuid.uuid4().hex}@example.test",
        display_name="Test",
        password_hash="!x",
    )
    session.add(user)
    session.flush()
    membership = OrganizationMembership(
        user_id=user.id, organization_id="o" * 32, role=role, status="active"
    )
    session.add(membership)
    session.flush()
    return membership


def test_granted_capability_beyond_role_is_allowed(db_session):
    membership = _membership(db_session, "member")
    assert "underwriting:approve" not in effective_capabilities(db_session, membership)
    db_session.add(
        MembershipPermission(
            membership_id=membership.id, capability="underwriting:approve", granted=True
        )
    )
    db_session.flush()
    resolved = effective_capabilities(db_session, membership)
    assert "underwriting:approve" in resolved
    # Grant is additive: the role's own defaults are preserved.
    assert role_default_capabilities("member") <= resolved


def test_revoked_capability_below_role_is_denied(db_session):
    membership = _membership(db_session, "admin")
    assert "governance:manage" in effective_capabilities(db_session, membership)
    db_session.add(
        MembershipPermission(
            membership_id=membership.id, capability="governance:manage", granted=False
        )
    )
    db_session.flush()
    assert "governance:manage" not in effective_capabilities(db_session, membership)


def test_deny_by_default_for_a_capability_outside_the_catalog(db_session):
    membership = _membership(db_session, "owner")
    resolved = effective_capabilities(db_session, membership)
    # An owner holds every catalog capability, yet an unknown capability is still denied.
    assert resolved == ALL_CAPABILITIES
    assert "nonexistent:capability" not in resolved
    principal = PrincipalContext(
        user_id="u",
        session_id="s",
        email="e@x.test",
        display_name="U",
        organization_id="o" * 32,
        membership_id=membership.id,
        role="owner",
        capabilities=tuple(sorted(resolved)),
    )
    assert principal.has_capability("ic:decide") is True
    assert principal.has_capability("nonexistent:capability") is False


def test_principal_without_resolved_capabilities_falls_back_to_role_defaults():
    principal = PrincipalContext(
        user_id="u",
        session_id="s",
        email="e@x.test",
        display_name="U",
        organization_id="o" * 32,
        membership_id="m",
        role="viewer",
    )
    assert principal.has_capability("workspace:read") is True
    assert principal.has_capability("workspace:write") is False


# --- require_capability dependency, unit-level ----------------------------------------------


def test_require_capability_dependency_semantics(monkeypatch):
    from fastapi import HTTPException

    from src.routers.deps import require_capability

    monkeypatch.setattr(settings, "permission_matrix_enabled", True)
    dependency = require_capability("underwriting:approve")

    class _Req:
        class state:  # noqa: N801 - minimal stand-in for Starlette request.state
            principal = None

    # No principal (auth-off/dev) -> no-op.
    assert dependency(_Req) is None

    # Member without the capability -> 403.
    _Req.state.principal = PrincipalContext(
        user_id="u", session_id="s", email="e@x.test", display_name="U",
        organization_id="o" * 32, membership_id="m", role="member",
    )
    with pytest.raises(HTTPException) as excinfo:
        dependency(_Req)
    assert excinfo.value.status_code == 403

    # Member granted the capability -> allowed.
    _Req.state.principal = PrincipalContext(
        user_id="u", session_id="s", email="e@x.test", display_name="U",
        organization_id="o" * 32, membership_id="m", role="member",
        capabilities=("underwriting:approve", "workspace:read"),
    )
    assert dependency(_Req) is None

    # Toggle off -> the fine-grained gate degrades to a no-op (coarse role guard only).
    monkeypatch.setattr(settings, "permission_matrix_enabled", False)
    _Req.state.principal = PrincipalContext(
        user_id="u", session_id="s", email="e@x.test", display_name="U",
        organization_id="o" * 32, membership_id="m", role="member",
    )
    assert dependency(_Req) is None


# --- end-to-end: capability gate on the QoE decision endpoint --------------------------------


@pytest.fixture(autouse=True)
def _production_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "permission_matrix_enabled", True)
    yield


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


def test_member_needs_underwriting_approve_capability_to_record_a_qoe_decision(client):
    from src.main import _auth_rate_limiter

    owner = _register(client, "perm-owner")
    org = owner["principal"]["organization_id"]
    owner_headers = _bearer(owner["access_token"])

    workspace = client.post(
        "/api/workspaces", json={"name": "Perm gate", "deal_type": "buyout"}, headers=owner_headers
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["id"]

    case = UnderwritingCaseCreate(
        case_key="base",
        label="Base case",
        assumptions=sample_assumptions(),
        created_by="perm-owner",
        change_note="Initial underwrite",
    ).model_dump(mode="json")
    created = client.post(
        f"/api/workspaces/{workspace_id}/underwriting/cases", json=case, headers=owner_headers
    )
    assert created.status_code == 201, created.text

    # A separate person, added to the owner's org as a plain member.
    member_credentials = _registration("perm-member")
    _auth_rate_limiter.clear()
    assert client.post("/api/auth/register", json=member_credentials).status_code == 201
    added = client.post(
        f"/api/organizations/{org}/members",
        json={"email": member_credentials["email"], "role": "member"},
        headers=owner_headers,
    )
    assert added.status_code == 201, added.text
    membership_id = added.json()["id"]

    _auth_rate_limiter.clear()
    login = client.post(
        "/api/auth/login",
        json={
            "email": member_credentials["email"],
            "password": member_credentials["password"],
            "organization_id": org,
        },
    )
    assert login.status_code == 200, login.text
    member_token = login.json()["access_token"]
    assert login.json()["principal"]["role"] == "member"

    decision_url = (
        f"/api/workspaces/{workspace_id}/underwriting/cases/base/versions/1/decisions"
    )
    decision_body = {
        "decision": "submitted",
        "actor": member_credentials["email"],
        "rationale": "Submitting for approval",
    }

    # Deny-by-default: a member lacks underwriting:approve -> 403.
    denied = client.post(decision_url, json=decision_body, headers=_bearer(member_token))
    assert denied.status_code == 403, denied.text
    assert "underwriting:approve" in denied.json()["detail"]

    # Owner grants the capability to this one membership.
    grant = client.put(
        f"/api/memberships/{membership_id}/permissions",
        json={"capability": "underwriting:approve", "granted": True},
        headers=owner_headers,
    )
    assert grant.status_code == 200, grant.text
    assert "underwriting:approve" in grant.json()["effective"]

    # Same session token now resolves the grant (capabilities are re-derived every request).
    allowed = client.post(decision_url, json=decision_body, headers=_bearer(member_token))
    assert allowed.status_code == 201, allowed.text

    # Revoking it again restores deny-by-default.
    revoke = client.put(
        f"/api/memberships/{membership_id}/permissions",
        json={"capability": "underwriting:approve", "granted": False},
        headers=owner_headers,
    )
    assert revoke.status_code == 200, revoke.text
    assert "underwriting:approve" not in revoke.json()["effective"]
    reblocked = client.post(decision_url, json=decision_body, headers=_bearer(member_token))
    assert reblocked.status_code == 403, reblocked.text


def test_unknown_capability_grant_is_rejected(client):
    owner = _register(client, "perm-unknown")
    # Owner needs a membership_id to target; reuse their own.
    me = client.get("/api/auth/me", headers=_bearer(owner["access_token"]))
    membership_id = me.json()["principal"]["membership_id"]
    bad = client.put(
        f"/api/memberships/{membership_id}/permissions",
        json={"capability": "made:up", "granted": True},
        headers=_bearer(owner["access_token"]),
    )
    assert bad.status_code == 400, bad.text
