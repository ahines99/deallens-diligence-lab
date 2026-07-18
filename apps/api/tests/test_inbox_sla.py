"""G78 — review-inbox SLAs: per-plane aging report over the same four planes as my_reviews.

Covers aging math with an injected ``now`` (100h vs a 72h SLA breaches; 10h does not),
four-eyes exclusions carrying into the aging view, per-plane threshold overrides, the digest's
compact breach summary, and the ``/my-reviews/aging`` endpoint.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base, new_uuid, now_utc
from src.models.deal_workflow import ICComment, ICPacket
from src.models.target import Target  # noqa: F401 - registers referenced table
from src.models.workspace import Workspace
from src.schemas.deal_workflow import ActorContext, DealCreate, FundCreate, OrganizationCreate
from src.schemas.underwriting_data import PrivateTargetCreate, QoEAdjustmentCreate
from src.services import deal_workflow_service as workflow
from src.services import notification_service
from src.services import review_inbox_service as inbox
from src.services import underwriting_data_service as underwriting

PROPOSER = "proposer-actor"
REVIEWER = "reviewer-actor"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _org_deal(db: Session, suffix: str):
    creator = ActorContext(actor_id=PROPOSER, display_name="Proposer")
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"SLA Org {suffix}", slug=f"sla-org-{suffix}"), creator
    )
    actor = creator.model_copy(update={"organization_id": organization.id})
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), actor)
    workspace = Workspace(
        id=new_uuid(),
        name=f"WS {suffix}",
        deal_type="buyout",
        investment_question="Buy?",
        status="draft",
    )
    db.add(workspace)
    db.flush()
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(
            code=f"SLA-{suffix}",
            name=f"Project {suffix}",
            target_company="Target Co",
            workspace_id=workspace.id,
        ),
        actor,
    )
    underwriting.create_private_target(
        db, workspace.id, PrivateTargetCreate(name=f"Target {suffix}")
    )
    return organization, actor, deal, workspace


def _propose_qoe(db: Session, workspace_id: str, *, created_by: str, title: str, age: timedelta,
                 now):
    adjustment = underwriting.create_qoe_adjustment(
        db,
        workspace_id,
        QoEAdjustmentCreate(
            period_end=date(2025, 12, 31),
            bridge_layer="management",
            title=title,
            amount=Decimal("100000"),
            created_by=created_by,
        ),
    )
    adjustment.created_at = now - age
    db.commit()
    return adjustment


def _blocking_comment(db: Session, deal_id: str, *, author: str, age: timedelta, now):
    packet = ICPacket(
        deal_id=deal_id,
        version=1,
        title="IC packet",
        content_hash="a" * 64,
        status="in_review",
    )
    db.add(packet)
    db.flush()
    comment = ICComment(
        packet_id=packet.id,
        body="Clarify the leverage assumption",
        blocking=True,
        status="open",
        author_actor_id=author,
    )
    db.add(comment)
    db.flush()
    comment.created_at = now - age
    db.commit()
    return comment


def test_aging_math_breaches_only_past_the_sla(db: Session):
    _org, actor, _deal, workspace = _org_deal(db, "math")
    now = now_utc()
    old = _propose_qoe(
        db, workspace.id, created_by=PROPOSER, title="Old add-back",
        age=timedelta(hours=100), now=now,
    )
    _propose_qoe(
        db, workspace.id, created_by=PROPOSER, title="Fresh add-back",
        age=timedelta(hours=10), now=now,
    )

    report = inbox.aging_report(db, actor.organization_id, REVIEWER, now=now)
    plane = report["planes"]["qoe"]
    assert plane["count"] == 2
    assert plane["sla_hours"] == 72.0
    assert plane["oldest_age_hours"] == 100.0
    assert [b["id"] for b in plane["breaches"]] == [old.id]  # 100h > 72h; 10h does not breach
    assert plane["breaches"][0] == {
        "id": old.id, "title": "Old add-back", "age_hours": 100.0, "sla_hours": 72.0
    }
    assert report["total"] == 2
    assert report["total_breaches"] == 1
    assert report["as_of"] == now
    assert report["sla_hours"] == inbox.DEFAULT_SLA_HOURS
    # The aging view counts exactly what my_reviews serves.
    assert report["planes"]["qoe"]["count"] == (
        inbox.my_reviews(db, actor.organization_id, REVIEWER)["counts_by_plane"]["qoe"]
    )


def test_four_eyes_exclusions_hold_in_the_aging_view(db: Session):
    _org, actor, _deal, workspace = _org_deal(db, "eyes")
    now = now_utc()
    _propose_qoe(
        db, workspace.id, created_by=PROPOSER, title="My own stale proposal",
        age=timedelta(hours=100), now=now,
    )

    # The item is 100h past creation, but it is NOT the proposer's breach: they cannot review it.
    proposer_report = inbox.aging_report(db, actor.organization_id, PROPOSER, now=now)
    assert proposer_report["planes"]["qoe"]["count"] == 0
    assert proposer_report["planes"]["qoe"]["breaches"] == []
    assert proposer_report["planes"]["qoe"]["oldest_age_hours"] is None
    assert proposer_report["total_breaches"] == 0

    reviewer_report = inbox.aging_report(db, actor.organization_id, REVIEWER, now=now)
    assert reviewer_report["planes"]["qoe"]["count"] == 1
    assert reviewer_report["total_breaches"] == 1


def test_per_plane_threshold_override_and_validation(db: Session):
    _org, actor, _deal, workspace = _org_deal(db, "override")
    now = now_utc()
    _propose_qoe(
        db, workspace.id, created_by=PROPOSER, title="Ten hours old",
        age=timedelta(hours=10), now=now,
    )

    default_report = inbox.aging_report(db, actor.organization_id, REVIEWER, now=now)
    assert default_report["total_breaches"] == 0  # 10h < 72h default

    tight = inbox.aging_report(
        db, actor.organization_id, REVIEWER, now=now, sla_hours={"qoe": 5}
    )
    assert tight["planes"]["qoe"]["sla_hours"] == 5.0
    assert tight["total_breaches"] == 1  # 10h > 5h override
    # Other planes keep their documented defaults.
    assert tight["planes"]["diligence"]["sla_hours"] == 120.0
    assert tight["planes"]["ic_comment"]["sla_hours"] == 48.0

    with pytest.raises(inbox.ReviewInboxError, match="Unknown review plane"):
        inbox.aging_report(
            db, actor.organization_id, REVIEWER, now=now, sla_hours={"bogus": 10}
        )
    with pytest.raises(inbox.ReviewInboxError, match="positive"):
        inbox.aging_report(
            db, actor.organization_id, REVIEWER, now=now, sla_hours={"qoe": 0}
        )
    with pytest.raises(inbox.ReviewInboxError):
        inbox.aging_report(db, actor.organization_id, None, now=now)


def test_ic_comment_plane_uses_its_own_default(db: Session):
    _org, actor, deal, _workspace = _org_deal(db, "comment")
    now = now_utc()
    stale = _blocking_comment(db, deal.id, author=PROPOSER, age=timedelta(hours=50), now=now)

    report = inbox.aging_report(db, actor.organization_id, REVIEWER, now=now)
    plane = report["planes"]["ic_comment"]
    assert plane["sla_hours"] == 48.0
    assert [b["id"] for b in plane["breaches"]] == [stale.id]  # 50h > 48h
    assert plane["breaches"][0]["age_hours"] == 50.0


def test_digest_inbox_block_carries_breach_counts(db: Session):
    organization, actor, _deal, workspace = _org_deal(db, "digest")
    now = now_utc()
    _propose_qoe(
        db, workspace.id, created_by=PROPOSER, title="Stale add-back",
        age=timedelta(hours=100), now=now,
    )
    _propose_qoe(
        db, workspace.id, created_by=PROPOSER, title="Fresh add-back",
        age=timedelta(hours=10), now=now,
    )

    digest = notification_service.build_digest(
        db, organization.id, user_id=REVIEWER, window="daily", now=now
    )
    assert digest["inbox"]["counts_by_plane"]["qoe"] == 2
    assert digest["inbox"]["oldest_age_hours"] == 100.0
    assert digest["inbox"]["sla"] == {
        "total_breaches": 1,
        "breaches_by_plane": {"qoe": 1, "claim": 0, "diligence": 0, "ic_comment": 0},
    }


def test_aging_endpoint_via_api(client):
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        _org, actor, _deal, workspace = _org_deal(session, "api")
        now = now_utc()
        _propose_qoe(
            session, workspace.id, created_by=PROPOSER, title="Endpoint add-back",
            age=timedelta(hours=10), now=now,
        )
        organization_id = actor.organization_id

    base = f"/api/organizations/{organization_id}/my-reviews/aging"
    response = client.get(base, params={"actor_id": REVIEWER})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actor_id"] == REVIEWER
    assert body["planes"]["qoe"]["count"] == 1
    assert body["planes"]["qoe"]["sla_hours"] == 72.0
    assert body["total_breaches"] == 0

    tight = client.get(base, params={"actor_id": REVIEWER, "qoe_sla_hours": 5})
    assert tight.status_code == 200, tight.text
    body = tight.json()
    assert body["planes"]["qoe"]["sla_hours"] == 5.0
    assert body["total_breaches"] == 1
    assert body["planes"]["qoe"]["breaches"][0]["title"] == "Endpoint add-back"

    invalid = client.get(base, params={"actor_id": REVIEWER, "qoe_sla_hours": 0})
    assert invalid.status_code == 422  # gt=0 enforced at the router

    missing_actor = client.get(base)
    assert missing_actor.status_code == 422
