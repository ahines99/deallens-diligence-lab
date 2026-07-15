"""G44 — read-only tokenized share links for a frozen workspace snapshot.

Covers plaintext-once/digest-at-rest, the non-confidential snapshot (safe default), expiry and
revocation (410), unknown tokens (404), cross-workspace isolation, cross-tenant creation guard, and
the public session-less read endpoint.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base, now_utc
from src.models.deal_workflow import Organization
from src.models.risk import RiskFinding
from src.models.share_link import ShareLink
from src.models.target import Target
from src.models.workspace import Workspace
from src.schemas.identity import PrincipalContext
from src.schemas.share_link import ShareLinkCreate
from src.services import share_link_service
from src.services.common import NotFound
from src.services.share_link_service import ShareLinkGone


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _principal(organization_id: str, role: str = "member") -> PrincipalContext:
    return PrincipalContext(
        user_id="user-1",
        session_id="sess",
        email="owner@corp.com",
        display_name="Owner",
        organization_id=organization_id,
        membership_id="mem",
        role=role,
    )


def _workspace(session: Session, organization_id: str, *, with_content: bool = True) -> Workspace:
    workspace = Workspace(
        name="Project Atlas",
        organization_id=organization_id,
        deal_type="buyout",
        investment_question="Is Atlas attractive?",
        status="complete",
        data_classification="confidential",
    )
    session.add(workspace)
    session.flush()
    if with_content:
        session.add(
            Target(
                workspace_id=workspace.id,
                name="Atlas Corp",
                ticker="ATLS",
                sector="Software",
                description="Vertical SaaS for logistics.",
                revenue=123_000_000.0,
                net_income=9_000_000.0,
            )
        )
        session.add(
            RiskFinding(
                workspace_id=workspace.id,
                risk_category="customer_concentration",
                risk_category_label="Customer concentration",
                title="Top-5 customers are 60% of revenue",
                finding="Concentrated revenue base.",
                severity="high",
                severity_score=8,
            )
        )
    session.commit()
    return workspace


def _setup(session: Session, slug: str = "atlas-cap"):
    organization = Organization(name=slug.title(), slug=slug)
    session.add(organization)
    session.flush()
    workspace = _workspace(session, organization.id)
    session.commit()
    return organization, workspace


def test_create_returns_plaintext_once_and_stores_only_the_digest(db: Session):
    organization, workspace = _setup(db)
    record, token = share_link_service.create_share_link(
        db, workspace.id, ShareLinkCreate(label="Interviewer walk-through"), _principal(organization.id)
    )
    assert token.startswith("dsh_")
    # Only the digest is persisted; the plaintext appears nowhere in the row.
    assert record.token_digest == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert record.token_digest != token
    assert record.scope == "read_only"
    assert record.label == "Interviewer walk-through"


def test_resolve_valid_token_returns_bound_workspace_and_scope(db: Session):
    organization, workspace = _setup(db)
    _record, token = share_link_service.create_share_link(
        db, workspace.id, ShareLinkCreate(), _principal(organization.id)
    )
    resolved = share_link_service.resolve_share_link(db, token)
    assert resolved.workspace_id == workspace.id
    assert resolved.scope == "read_only"
    assert resolved.last_accessed_at is not None


def test_snapshot_excludes_confidential_content(db: Session):
    organization, workspace = _setup(db)
    record, _token = share_link_service.create_share_link(
        db, workspace.id, ShareLinkCreate(), _principal(organization.id)
    )
    snapshot = share_link_service.build_snapshot(db, record)

    # Public research artifacts are present...
    assert snapshot["scope"] == "read_only"
    assert snapshot["target"]["name"] == "Atlas Corp"
    assert snapshot["target"]["ticker"] == "ATLS"
    assert snapshot["risks"][0]["title"].startswith("Top-5 customers")
    # ...but confidential financials are never exposed.
    assert "revenue" not in snapshot["target"]
    assert "financials" not in snapshot["target"]
    assert "net_income" not in snapshot["target"]
    flat = repr(snapshot)
    assert "123000000" not in flat and "9000000" not in flat


def test_expired_token_is_gone(db: Session):
    organization, workspace = _setup(db)
    record, token = share_link_service.create_share_link(
        db, workspace.id, ShareLinkCreate(), _principal(organization.id)
    )
    record.expires_at = now_utc() - timedelta(hours=1)
    db.commit()
    with pytest.raises(ShareLinkGone) as exc:
        share_link_service.resolve_share_link(db, token)
    assert exc.value.status_code == 410


def test_revoked_token_is_gone(db: Session):
    organization, workspace = _setup(db)
    record, token = share_link_service.create_share_link(
        db, workspace.id, ShareLinkCreate(), _principal(organization.id)
    )
    share_link_service.revoke_share_link(db, record.id, _principal(organization.id))
    with pytest.raises(ShareLinkGone) as exc:
        share_link_service.resolve_share_link(db, token)
    assert exc.value.status_code == 410


def test_unknown_or_malformed_token_is_not_found(db: Session):
    with pytest.raises(NotFound):
        share_link_service.resolve_share_link(db, "dsh_" + "z" * 40)
    with pytest.raises(NotFound):
        share_link_service.resolve_share_link(db, "not-a-share-token")


def test_cross_workspace_token_reads_only_its_own_workspace(db: Session):
    organization, workspace_a = _setup(db, "org-a")
    workspace_b = _workspace(db, organization.id)
    record_a, token_a = share_link_service.create_share_link(
        db, workspace_a.id, ShareLinkCreate(), _principal(organization.id)
    )
    resolved = share_link_service.resolve_share_link(db, token_a)
    assert resolved.workspace_id == workspace_a.id
    assert resolved.workspace_id != workspace_b.id
    snapshot = share_link_service.build_snapshot(db, record_a)
    assert snapshot["workspace"]["name"] == "Project Atlas"


def test_create_on_foreign_tenant_workspace_is_not_found(db: Session):
    _organization, workspace = _setup(db, "owner-org")
    other = Organization(name="Other", slug="other-org")
    db.add(other)
    db.commit()
    with pytest.raises(NotFound):
        share_link_service.create_share_link(
            db, workspace.id, ShareLinkCreate(), _principal(other.id)
        )


def test_shared_public_endpoint_reads_then_404_after_revocation(client):
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        organization = Organization(name="Public Share Org", slug="public-share-org")
        session.add(organization)
        session.flush()
        workspace = _workspace(session, organization.id)
        record, token = share_link_service.create_share_link(
            session, workspace.id, ShareLinkCreate(label="demo"), _principal(organization.id)
        )
        share_link_id = record.id

    # Public read: no Authorization header required.
    ok = client.get(f"/api/shared/{token}")
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["target"]["name"] == "Atlas Corp"
    assert "financials" not in payload["target"]

    # Unknown token -> 404.
    assert client.get("/api/shared/dsh_missingtokenmissingtoken0001").status_code == 404

    # Revoke, then the public read is Gone (410).
    with SessionLocal() as session:
        share_link_service.revoke_share_link(
            session, share_link_id, _principal(_org_id_of(session, share_link_id))
        )
    gone = client.get(f"/api/shared/{token}")
    assert gone.status_code == 410


def _org_id_of(session, share_link_id: str) -> str:
    return session.get(ShareLink, share_link_id).organization_id
