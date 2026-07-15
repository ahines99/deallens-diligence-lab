"""G33 — in-app notification center fed by the workflow audit outbox.

Covers event-to-notification mapping (title/type/entity), idempotent dedup by
`source_audit_event_id`, `mark_read`/`unread_count`, tenant scoping, and the HTTP endpoints.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base
from src.schemas.deal_workflow import ActorContext, DealCreate, FundCreate, OrganizationCreate
from src.services import deal_workflow_service as workflow
from src.services import notification_service
from src.services.common import NotFound


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _setup(db: Session, suffix: str = "one"):
    creator = ActorContext(actor_id=f"lead-{suffix}", display_name="Deal Lead")
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"Org {suffix}", slug=f"notif-org-{suffix}"), creator
    )
    actor = creator.model_copy(update={"organization_id": organization.id})
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), actor)
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(code=f"P-{suffix}", name=f"Project {suffix}", target_company="Target"),
        actor,
    )
    return actor, organization, fund, deal


def test_audit_events_map_to_notifications_with_titles(db: Session):
    _actor, organization, _fund, deal = _setup(db)
    created = notification_service.sync_from_audit(db, organization.id)

    by_type = {n.event_type: n for n in created}
    assert set(by_type) == {"organization.created", "fund.created", "deal.created"}

    deal_note = by_type["deal.created"]
    assert deal_note.title == "Deal created"
    assert deal_note.entity_type == "Deal"
    assert deal_note.entity_id == deal.id
    assert deal_note.source_audit_event_id
    assert "Deal Lead" in deal_note.body  # actor attribution carried into the body
    assert deal_note.read_at is None


def test_unmapped_action_gets_humanized_fallback(db: Session):
    from src.models.deal_workflow import WorkflowAuditEvent

    _actor, organization, _fund, deal = _setup(db)
    event = WorkflowAuditEvent(
        organization_id=organization.id,
        deal_id=deal.id,
        action="deal.some_new_action",
        entity_type="Deal",
        entity_id=deal.id,
        detail={},
    )
    db.add(event)
    db.commit()

    created = notification_service.sync_from_audit(db, organization.id)
    fallback = next(n for n in created if n.event_type == "deal.some_new_action")
    assert fallback.title == "Deal some new action"


def test_sync_is_idempotent_and_dedups_by_source_event(db: Session):
    _actor, organization, _fund, _deal = _setup(db)

    first = notification_service.sync_from_audit(db, organization.id)
    assert len(first) == 3

    second = notification_service.sync_from_audit(db, organization.id)
    assert second == []

    assert len(notification_service.list_notifications(db, organization.id)) == 3


def test_mark_read_flips_read_at_and_unread_count(db: Session):
    _actor, organization, _fund, _deal = _setup(db)
    notification_service.sync_from_audit(db, organization.id)
    assert notification_service.unread_count(db, organization.id) == 3

    target = notification_service.list_notifications(db, organization.id, unread_only=True)[0]
    assert target.read_at is None

    updated = notification_service.mark_read(db, target.id, organization.id)
    assert updated.read_at is not None
    assert notification_service.unread_count(db, organization.id) == 2

    # A second mark_read is a no-op that keeps the original timestamp (idempotent).
    again = notification_service.mark_read(db, target.id, organization.id)
    assert again.read_at == updated.read_at

    remaining = {n.id for n in notification_service.list_notifications(db, organization.id, unread_only=True)}
    assert target.id not in remaining


def test_notifications_are_tenant_scoped(db: Session):
    _actor_a, org_a, _f_a, _d_a = _setup(db, "alpha")
    _actor_b, org_b, _f_b, _d_b = _setup(db, "beta")

    notification_service.sync_from_audit(db, org_a.id)
    # org_b has its own audit events but no notifications synced yet, and never sees org_a's.
    assert notification_service.list_notifications(db, org_b.id) == []

    created_b = notification_service.sync_from_audit(db, org_b.id)
    assert created_b and all(n.organization_id == org_b.id for n in created_b)


def test_mark_read_cross_org_is_not_found(db: Session):
    _actor_a, org_a, _f_a, _d_a = _setup(db, "alpha")
    _setup(db, "beta")
    notification_service.sync_from_audit(db, org_a.id)
    note = notification_service.list_notifications(db, org_a.id)[0]

    with pytest.raises(NotFound):
        notification_service.mark_read(db, note.id, "f" * 32)


def test_notification_endpoints_via_api(client):
    """The HTTP surface lists notifications, reports unread count, and marks one read."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        creator = ActorContext(actor_id="api-lead", display_name="API Lead")
        organization = workflow.create_organization(
            session, OrganizationCreate(name="API Notif Org", slug="api-notif-org"), creator
        )
        actor = creator.model_copy(update={"organization_id": organization.id})
        fund = workflow.create_fund(session, organization.id, FundCreate(name="Fund I"), actor)
        workflow.create_deal(
            session,
            fund.id,
            DealCreate(code="API-1", name="API Deal", target_company="Target"),
            actor,
        )
        organization_id = organization.id

    listed = client.get(f"/api/organizations/{organization_id}/notifications")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert len(body) == 3
    assert {n["event_type"] for n in body} == {
        "organization.created",
        "fund.created",
        "deal.created",
    }

    count = client.get(f"/api/organizations/{organization_id}/notifications/unread-count")
    assert count.json() == {"organization_id": organization_id, "unread": 3}

    read = client.post(f"/api/notifications/{body[0]['id']}/read")
    assert read.status_code == 200
    assert read.json()["read_at"] is not None

    after = client.get(f"/api/organizations/{organization_id}/notifications/unread-count")
    assert after.json()["unread"] == 2

    unread_only = client.get(
        f"/api/organizations/{organization_id}/notifications", params={"unread_only": True}
    )
    assert len(unread_only.json()) == 2
