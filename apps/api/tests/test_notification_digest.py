"""G77 — per-user notification digests computed on read (no schema, no delivery state).

Covers window boundaries with an injected ``now``, directed-notification privacy (a member's
mention NEVER appears in anyone else's digest — mirroring
``test_directed_mention_notification_is_private_to_its_recipient``), summary-not-redelivery
semantics (``read_at`` untouched, idempotent), and the HTTP endpoint.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base, now_utc
from src.models.deal_workflow import Organization
from src.models.identity import OrganizationMembership, User
from src.schemas.comment import CommentCreate
from src.schemas.deal_workflow import ActorContext, DealCreate, FundCreate, OrganizationCreate
from src.schemas.identity import PrincipalContext
from src.services import comment_service, notification_service
from src.services import deal_workflow_service as workflow


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _workflow_org(db: Session, suffix: str = "one"):
    creator = ActorContext(actor_id=f"lead-{suffix}", display_name="Deal Lead")
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"Digest Org {suffix}", slug=f"digest-org-{suffix}"), creator
    )
    actor = creator.model_copy(update={"organization_id": organization.id})
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), actor)
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(code=f"D-{suffix}", name=f"Digest {suffix}", target_company="Target"),
        actor,
    )
    return actor, organization, fund, deal


def _user(session: Session, handle: str, role: str, organization_id: str) -> User:
    user = User(
        email=f"{handle}@corp.com",
        email_normalized=f"{handle}@corp.com",
        display_name=handle.title(),
        password_hash="x",
    )
    session.add(user)
    session.flush()
    session.add(
        OrganizationMembership(
            user_id=user.id, organization_id=organization_id, role=role, status="active"
        )
    )
    session.flush()
    return user


def _principal(user: User, organization_id: str, role: str = "member") -> PrincipalContext:
    return PrincipalContext(
        user_id=user.id,
        session_id="sess",
        email=user.email,
        display_name=user.display_name,
        organization_id=organization_id,
        membership_id="mem",
        role=role,
    )


def _comment_org(session: Session, slug: str = "acme"):
    organization = Organization(name=slug.title(), slug=slug)
    session.add(organization)
    session.flush()
    author = _user(session, f"author-{slug}", "member", organization.id)
    alice = _user(session, f"alice-{slug}", "member", organization.id)
    viewer = _user(session, f"viewer-{slug}", "viewer", organization.id)
    session.commit()
    return organization, author, alice, viewer


def test_window_boundaries_are_half_open_and_deterministic(db: Session):
    _actor, organization, _fund, _deal = _workflow_org(db)
    notification_service.sync_from_audit(db, organization.id)
    now = now_utc()
    ages = {
        "deal.created": timedelta(hours=1),        # inside daily
        "fund.created": timedelta(hours=24),       # exactly one window old => aged OUT of daily
        "organization.created": timedelta(hours=25),  # outside daily, inside weekly
    }
    for row in notification_service.list_notifications(db, organization.id):
        row.created_at = now - ages[row.event_type]
    db.commit()

    daily = notification_service.build_digest(
        db, organization.id, user_id=None, window="daily", now=now
    )
    assert daily["since"] == now - timedelta(days=1)
    assert daily["until"] == now
    assert daily["total"] == 1
    assert [group["event_type"] for group in daily["by_event_type"]] == ["deal.created"]
    assert daily["by_event_type"][0] == {
        "event_type": "deal.created",
        "count": 1,
        "latest_title": "Deal created",
    }

    weekly = notification_service.build_digest(
        db, organization.id, user_id=None, window="weekly", now=now
    )
    assert weekly["since"] == now - timedelta(days=7)
    assert weekly["total"] == 3
    # Equal counts fall back to event-type order, so the grouping is stable.
    assert [group["event_type"] for group in weekly["by_event_type"]] == [
        "deal.created",
        "fund.created",
        "organization.created",
    ]

    # Deterministic given the same injected now.
    assert weekly == notification_service.build_digest(
        db, organization.id, user_id=None, window="weekly", now=now
    )

    with pytest.raises(ValueError, match="window"):
        notification_service.build_digest(
            db, organization.id, user_id=None, window="hourly", now=now
        )


def test_directed_mention_never_appears_in_another_members_digest(db: Session):
    """Regression style, mirroring test_directed_mention_notification_is_private_to_its_recipient:
    the digest reuses the same recipient filter, so a directed row rolls up ONLY for its
    recipient."""
    organization, author, alice, viewer = _comment_org(db)
    comment_service.create_comment(
        db,
        CommentCreate(
            entity_type="risk", entity_id="risk-7", body="@alice-acme please take a look"
        ),
        _principal(author, organization.id),
    )
    notification_service.sync_from_audit(db, organization.id)
    now = now_utc()

    alice_digest = notification_service.build_digest(
        db, organization.id, user_id=alice.id, window="daily", now=now
    )
    alice_types = {group["event_type"] for group in alice_digest["by_event_type"]}
    assert "comment.mentioned" in alice_types
    assert "comment.created" in alice_types  # broadcast rows roll up for everyone
    assert alice_digest["directed_count"] == 1

    for other_user_id in (viewer.id, author.id, None):
        digest = notification_service.build_digest(
            db, organization.id, user_id=other_user_id, window="daily", now=now
        )
        types = {group["event_type"] for group in digest["by_event_type"]}
        assert "comment.mentioned" not in types
        assert digest["directed_count"] == 0
        # Not even the directed row's title leaks through a group summary.
        assert "You were mentioned" not in {
            group["latest_title"] for group in digest["by_event_type"]
        }
        assert digest["total"] == alice_digest["total"] - 1


def test_digest_is_a_summary_not_a_redelivery(db: Session):
    _actor, organization, _fund, _deal = _workflow_org(db, suffix="dedup")
    notification_service.sync_from_audit(db, organization.id)
    now = now_utc()

    unread_before = notification_service.unread_count(db, organization.id)
    first = notification_service.build_digest(
        db, organization.id, user_id=None, window="daily", now=now
    )
    second = notification_service.build_digest(
        db, organization.id, user_id=None, window="daily", now=now
    )
    assert first == second  # idempotent: nothing is consumed or re-delivered
    assert first["total"] == unread_before

    # The live list is untouched: every row is still unread with read_at unset.
    assert notification_service.unread_count(db, organization.id) == unread_before
    rows = notification_service.list_notifications(db, organization.id)
    assert all(row.read_at is None for row in rows)
    assert len(rows) == unread_before


def test_digest_shape_includes_inbox_sla_block(db: Session):
    _actor, organization, _fund, _deal = _workflow_org(db, suffix="shape")
    notification_service.sync_from_audit(db, organization.id)
    digest = notification_service.build_digest(
        db, organization.id, user_id="reviewer-1", window="daily", now=now_utc()
    )
    assert digest["organization_id"] == organization.id
    assert digest["user_id"] == "reviewer-1"
    assert digest["window"] == "daily"
    assert digest["inbox"]["total"] == 0
    assert digest["inbox"]["counts_by_plane"] == {
        "qoe": 0, "claim": 0, "diligence": 0, "ic_comment": 0
    }
    assert digest["inbox"]["sla"] == {
        "total_breaches": 0,
        "breaches_by_plane": {"qoe": 0, "claim": 0, "diligence": 0, "ic_comment": 0},
    }


def test_digest_endpoint_via_api(client):
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        creator = ActorContext(actor_id="digest-api-lead", display_name="Digest Lead")
        organization = workflow.create_organization(
            session,
            OrganizationCreate(name="Digest API Org", slug="digest-api-org"),
            creator,
        )
        actor = creator.model_copy(update={"organization_id": organization.id})
        fund = workflow.create_fund(session, organization.id, FundCreate(name="Fund I"), actor)
        workflow.create_deal(
            session,
            fund.id,
            DealCreate(code="DG-1", name="Digest Deal", target_company="Target"),
            actor,
        )
        organization_id = organization.id

    response = client.get(
        f"/api/organizations/{organization_id}/notifications/digest",
        params={"window": "weekly"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"] == organization_id
    assert body["window"] == "weekly"
    assert body["total"] == 3
    assert {group["event_type"] for group in body["by_event_type"]} == {
        "organization.created",
        "fund.created",
        "deal.created",
    }
    assert body["directed_count"] == 0
    assert body["inbox"]["sla"]["total_breaches"] == 0

    # The digest endpoint never mutates read state.
    count = client.get(f"/api/organizations/{organization_id}/notifications/unread-count")
    assert count.json()["unread"] == 3

    invalid = client.get(
        f"/api/organizations/{organization_id}/notifications/digest",
        params={"window": "hourly"},
    )
    assert invalid.status_code == 422
