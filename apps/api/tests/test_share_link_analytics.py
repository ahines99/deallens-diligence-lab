"""G76 — share-link view analytics + watermarking.

Covers: a view event recorded on every successful public snapshot read (coarse context only,
user-agent truncated); NO event on revoked/expired/unknown tokens; the owner analytics shape
(count, first/last, newest-first recent capped at 20) with the revocation state riding along;
org scoping (cross-tenant 404, service and endpoint level); the server-composed watermark in
the public payload; analytics never leaking through the public route; and the best-effort
insert contract (a missing table must not break the share view).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base, now_utc
from src.models.deal_workflow import Organization
from src.models.share_link import ShareLink
from src.models.share_link_view import ShareLinkView
from src.models.workspace import Workspace
from src.schemas.identity import PrincipalContext
from src.schemas.share_link import ShareLinkCreate
from src.services import share_link_service
from src.services.common import NotFound


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _principal(organization_id: str) -> PrincipalContext:
    return PrincipalContext(
        user_id="user-1",
        session_id="sess",
        email="owner@corp.com",
        display_name="Owner",
        organization_id=organization_id,
        membership_id="mem",
        role="member",
    )


def _setup(session: Session, slug: str = "atlas-cap"):
    organization = Organization(name=slug.replace("-", " ").title(), slug=slug)
    session.add(organization)
    session.flush()
    workspace = Workspace(
        name="Project Atlas",
        organization_id=organization.id,
        deal_type="buyout",
        investment_question="Is Atlas attractive?",
        status="complete",
        data_classification="confidential",
    )
    session.add(workspace)
    session.commit()
    return organization, workspace


def _link(session: Session, workspace, organization) -> tuple[ShareLink, str]:
    return share_link_service.create_share_link(
        session, workspace.id, ShareLinkCreate(label="analytics"), _principal(organization.id)
    )


def _add_view(session: Session, share_link_id: str, *, minutes_ago: int, agent: str) -> None:
    session.add(
        ShareLinkView(
            share_link_id=share_link_id,
            viewed_at=now_utc() - timedelta(minutes=minutes_ago),
            user_agent=agent,
            client_host="203.0.113.7",
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# Service-level: analytics shape, scoping, cap/ordering, watermark
# ---------------------------------------------------------------------------


def test_analytics_shape_includes_counts_bounds_and_revocation_state(db: Session):
    organization, workspace = _setup(db)
    record, _token = _link(db, workspace, organization)
    _add_view(db, record.id, minutes_ago=30, agent="agent-old")
    _add_view(db, record.id, minutes_ago=10, agent="agent-new")

    analytics = share_link_service.get_share_link_analytics(db, record.id, _principal(organization.id))

    assert analytics["view_count"] == 2
    assert analytics["first_viewed_at"] < analytics["last_viewed_at"]
    assert [v["user_agent"] for v in analytics["recent"]] == ["agent-new", "agent-old"]
    # Revocation state rides along so the UI shows views + one-click revoke together.
    assert analytics["share_link"].revoked_at is None
    share_link_service.revoke_share_link(db, record.id, _principal(organization.id))
    revoked = share_link_service.get_share_link_analytics(db, record.id, _principal(organization.id))
    assert revoked["share_link"].revoked_at is not None
    assert revoked["view_count"] == 2  # history survives revocation


def test_analytics_zero_views_reports_honest_empties(db: Session):
    organization, workspace = _setup(db)
    record, _token = _link(db, workspace, organization)
    analytics = share_link_service.get_share_link_analytics(db, record.id, _principal(organization.id))
    assert analytics["view_count"] == 0
    assert analytics["first_viewed_at"] is None
    assert analytics["last_viewed_at"] is None
    assert analytics["recent"] == []


def test_analytics_is_org_scoped_cross_tenant_is_not_found(db: Session):
    organization, workspace = _setup(db)
    record, _token = _link(db, workspace, organization)
    other = Organization(name="Other Capital", slug="other-cap")
    db.add(other)
    db.commit()
    with pytest.raises(NotFound):
        share_link_service.get_share_link_analytics(db, record.id, _principal(other.id))
    with pytest.raises(NotFound):
        share_link_service.get_share_link_analytics(db, "missing-id", _principal(organization.id))


def test_recent_is_capped_at_20_newest_first_but_count_is_total(db: Session):
    organization, workspace = _setup(db)
    record, _token = _link(db, workspace, organization)
    for i in range(25):
        _add_view(db, record.id, minutes_ago=25 - i, agent=f"agent-{i:02d}")

    analytics = share_link_service.get_share_link_analytics(db, record.id, _principal(organization.id))

    assert analytics["view_count"] == 25
    assert len(analytics["recent"]) == 20
    # Newest first: agent-24 was the most recent insert (fewest minutes ago).
    agents = [v["user_agent"] for v in analytics["recent"]]
    assert agents == [f"agent-{i:02d}" for i in range(24, 4, -1)]


def test_watermark_is_server_composed_from_org_link_and_date(db: Session):
    organization, workspace = _setup(db, "atlas-cap")
    record, _token = _link(db, workspace, organization)
    snapshot = share_link_service.build_snapshot(db, record)

    watermark = snapshot["watermark"]
    assert watermark == share_link_service.compose_watermark(db, record)
    assert "Shared read-only" in watermark
    assert organization.name in watermark
    assert record.token_digest[:8] in watermark
    assert record.created_at.date().isoformat() in watermark
    # Identifies the link WITHOUT weakening the secret: never the full digest or a raw token.
    assert record.token_digest not in watermark
    assert "dsh_" not in watermark


# ---------------------------------------------------------------------------
# Endpoint-level: view capture on the public route, negative paths, best-effort
# ---------------------------------------------------------------------------


def _api_setup(label: str) -> tuple[str, str, str]:
    """Create org + workspace + link in the app database; return (link_id, token, org_id)."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        organization, workspace = _setup(session, f"{label}-{uuid.uuid4().hex[:8]}")
        record, token = _link(session, workspace, organization)
        return record.id, token, organization.id


def _view_count(share_link_id: str) -> int:
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        return len(
            list(
                session.scalars(
                    select(ShareLinkView).where(ShareLinkView.share_link_id == share_link_id)
                )
            )
        )


def test_public_read_records_one_coarse_view_event(client):
    from src.db.session import SessionLocal

    link_id, token, _org = _api_setup("views")
    long_agent = "M" * 300  # must be stored truncated to 200, mirroring session discipline

    response = client.get(f"/api/shared/{token}", headers={"User-Agent": long_agent})
    assert response.status_code == 200, response.text

    with SessionLocal() as session:
        views = list(
            session.scalars(select(ShareLinkView).where(ShareLinkView.share_link_id == link_id))
        )
    assert len(views) == 1
    assert views[0].viewed_at is not None
    assert views[0].user_agent == "M" * 200
    # Coarse "where" only: the transport client host as seen by the server, nothing richer.
    assert views[0].client_host is not None


def test_revoked_and_unknown_tokens_record_no_view(client):
    link_id, token, org_id = _api_setup("negative")
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        share_link_service.revoke_share_link(session, link_id, _principal(org_id))

    assert client.get(f"/api/shared/{token}").status_code == 410
    assert client.get("/api/shared/dsh_missingtokenmissingtoken0001").status_code == 404
    assert _view_count(link_id) == 0


def test_public_payload_carries_watermark_and_never_analytics(client):
    _link_id, token, _org = _api_setup("watermark")
    response = client.get(f"/api/shared/{token}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["watermark"].startswith("Shared read-only")
    # The public route never exposes analytics or viewer context.
    flat = repr(payload)
    assert "view_count" not in flat
    assert "client_host" not in flat
    assert "recent" not in payload


def test_view_insert_failure_never_breaks_the_share_view(client):
    """Best-effort contract: drop the events table — the public read must still serve 200."""
    from src.db.session import SessionLocal, engine

    link_id, token, _org = _api_setup("besteffort")
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE share_link_views"))
    try:
        response = client.get(f"/api/shared/{token}")
        assert response.status_code == 200, response.text
        assert response.json()["watermark"].startswith("Shared read-only")
    finally:
        ShareLinkView.__table__.create(bind=engine)

    # And with the table restored, the same link records views again.
    assert client.get(f"/api/shared/{token}").status_code == 200
    assert _view_count(link_id) == 1
    # The session factory stays healthy after the swallowed failure.
    with SessionLocal() as session:
        assert session.get(ShareLink, link_id) is not None


def test_analytics_endpoint_is_org_scoped(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    owner = _register(client, "linkowner")
    outsider = _register(client, "outsider")
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    outsider_headers = {"Authorization": f"Bearer {outsider['access_token']}"}
    owner_org = owner["principal"]["organization_id"]

    from src.db.session import SessionLocal

    with SessionLocal() as session:
        workspace = Workspace(
            name="Endpoint Analytics WS",
            organization_id=owner_org,
            deal_type="buyout",
            investment_question="?",
            status="complete",
            data_classification="confidential",
        )
        session.add(workspace)
        session.commit()
        record, token = share_link_service.create_share_link(
            session, workspace.id, ShareLinkCreate(label="ep"), _principal(owner_org)
        )
        link_id = record.id

    assert client.get(f"/api/shared/{token}").status_code == 200  # records one view

    ok = client.get(f"/api/share-links/{link_id}/analytics", headers=owner_headers)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["view_count"] == 1
    assert body["first_viewed_at"] is not None
    assert body["last_viewed_at"] is not None
    assert len(body["recent"]) == 1
    assert set(body["recent"][0]) == {"viewed_at", "user_agent"}  # never client_host
    assert body["share_link"]["id"] == link_id
    assert body["share_link"]["revoked_at"] is None

    # Cross-tenant is a non-enumerable 404; anonymous is 401.
    assert (
        client.get(f"/api/share-links/{link_id}/analytics", headers=outsider_headers).status_code
        == 404
    )
    assert client.get(f"/api/share-links/{link_id}/analytics").status_code == 401


def _register(client, label: str) -> dict:
    suffix = uuid.uuid4().hex[:10]
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{label}-{suffix}@example.test",
            "display_name": f"{label.title()} Analyst",
            "password": "correct horse portfolio battery",
            "organization_name": f"{label.title()} Capital {suffix}",
            "organization_slug": f"{label}-capital-{suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
