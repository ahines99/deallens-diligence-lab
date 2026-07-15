"""G42 — offline coverage for the cross-plane "My reviews" inbox with four-eyes filtering."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base, new_uuid
from src.models.deal_intelligence import StructuredClaim
from src.models.deal_workflow import ICComment, ICPacket
from src.models.target import Target  # noqa: F401 - registers referenced table
from src.models.workspace import Workspace
from src.schemas.deal_workflow import (
    ActorContext,
    DealCreate,
    DiligenceRequestCreate,
    DiligenceResponseCreate,
    FundCreate,
    OrganizationCreate,
)
from src.schemas.underwriting_data import PrivateTargetCreate, QoEAdjustmentCreate
from src.services import deal_workflow_service as workflow
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
    """Create an org+fund+deal with a bound workspace + private target, return (actor, deal, ws)."""
    creator = ActorContext(actor_id=PROPOSER, display_name="Proposer")
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"Org {suffix}", slug=f"org-{suffix}"), creator
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
            code=f"D-{suffix}",
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


def _propose_qoe(db: Session, workspace_id: str, *, created_by: str, title: str = "Owner add-back"):
    return underwriting.create_qoe_adjustment(
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


def _add_claim(db: Session, deal_id: str, *, author: str, status: str = "unreviewed"):
    claim = StructuredClaim(
        deal_id=deal_id,
        logical_claim_id=new_uuid(),
        revision=1,
        document_id=new_uuid(),
        chunk_id=new_uuid(),
        category="kpi",
        field_name="revenue",
        value_text="Revenue was $120 million.",
        value_number=120.0,
        unit="USD_millions",
        period="FY2025",
        currency="USD",
        confidence=0.9,
        source_locator={},
        source_span={"start": 0, "end": 5, "text": "Reven"},
        review_status=status,
        extraction_version="test",
        created_by_actor_id=author,
    )
    db.add(claim)
    db.flush()
    return claim


def _respond_diligence(db: Session, deal_id: str, actor: ActorContext, *, responder: str):
    request = workflow.create_diligence_request(
        db,
        deal_id,
        DiligenceRequestCreate(title="Provide contracts", question="Share top contracts", send_now=True),
        actor,
    )
    responder_actor = actor.model_copy(update={"actor_id": responder, "display_name": responder})
    workflow.add_diligence_response(
        db, request.id, DiligenceResponseCreate(response_text="Attached."), responder_actor
    )
    return request


def _blocking_comment(db: Session, deal_id: str, *, author: str):
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
    return comment


def test_proposed_qoe_appears_for_a_different_actor_but_not_the_proposer(db: Session):
    _org, actor, deal, workspace = _org_deal(db, "qoe")
    adjustment = _propose_qoe(db, workspace.id, created_by=PROPOSER)

    reviewer_view = inbox.my_reviews(db, actor.organization_id, REVIEWER)
    qoe_ids = [item["id"] for item in reviewer_view["items"] if item["plane"] == "qoe"]
    assert adjustment.id in qoe_ids
    assert reviewer_view["counts_by_plane"]["qoe"] == 1

    # Four-eyes: the proposer must never see their own proposal in the queue.
    proposer_view = inbox.my_reviews(db, actor.organization_id, PROPOSER)
    assert all(item["id"] != adjustment.id for item in proposer_view["items"])
    assert proposer_view["counts_by_plane"]["qoe"] == 0


def test_unreviewed_claim_appears_for_a_non_author_only(db: Session):
    _org, actor, deal, _ws = _org_deal(db, "claim")
    claim = _add_claim(db, deal.id, author=PROPOSER)

    reviewer_view = inbox.my_reviews(db, actor.organization_id, REVIEWER)
    assert claim.id in [item["id"] for item in reviewer_view["items"] if item["plane"] == "claim"]

    author_view = inbox.my_reviews(db, actor.organization_id, PROPOSER)
    assert all(item["id"] != claim.id for item in author_view["items"])


def test_diligence_response_awaits_a_reviewer_other_than_the_responder(db: Session):
    _org, actor, deal, _ws = _org_deal(db, "dilig")
    request = _respond_diligence(db, deal.id, actor, responder=PROPOSER)

    reviewer_view = inbox.my_reviews(db, actor.organization_id, REVIEWER)
    assert request.id in [
        item["id"] for item in reviewer_view["items"] if item["plane"] == "diligence"
    ]
    # The response author cannot accept their own response.
    responder_view = inbox.my_reviews(db, actor.organization_id, PROPOSER)
    assert all(item["id"] != request.id for item in responder_view["items"])


def test_blocking_ic_comment_awaits_a_second_actor(db: Session):
    _org, actor, deal, _ws = _org_deal(db, "comment")
    comment = _blocking_comment(db, deal.id, author=PROPOSER)

    reviewer_view = inbox.my_reviews(db, actor.organization_id, REVIEWER)
    assert comment.id in [
        item["id"] for item in reviewer_view["items"] if item["plane"] == "ic_comment"
    ]
    author_view = inbox.my_reviews(db, actor.organization_id, PROPOSER)
    assert all(item["id"] != comment.id for item in author_view["items"])


def test_counts_by_plane_span_all_four_planes(db: Session):
    _org, actor, deal, workspace = _org_deal(db, "all")
    _propose_qoe(db, workspace.id, created_by=PROPOSER)
    _add_claim(db, deal.id, author=PROPOSER)
    _respond_diligence(db, deal.id, actor, responder=PROPOSER)
    _blocking_comment(db, deal.id, author=PROPOSER)

    view = inbox.my_reviews(db, actor.organization_id, REVIEWER)
    assert view["counts_by_plane"] == {"qoe": 1, "claim": 1, "diligence": 1, "ic_comment": 1}
    assert view["total"] == 4
    assert {item["plane"] for item in view["items"]} == {"qoe", "claim", "diligence", "ic_comment"}


def test_inbox_is_organization_scoped(db: Session):
    _org_a, actor_a, deal_a, workspace_a = _org_deal(db, "a")
    _propose_qoe(db, workspace_a.id, created_by=PROPOSER)
    _add_claim(db, deal_a.id, author=PROPOSER)

    _org_b, actor_b, _deal_b, _ws_b = _org_deal(db, "b")

    # Org B has no pending work; its inbox for the same reviewer is empty.
    view_b = inbox.my_reviews(db, actor_b.organization_id, REVIEWER)
    assert view_b["total"] == 0
    assert view_b["counts_by_plane"] == {"qoe": 0, "claim": 0, "diligence": 0, "ic_comment": 0}

    # Org A's items never leak into org B's queue.
    view_a = inbox.my_reviews(db, actor_a.organization_id, REVIEWER)
    assert view_a["total"] == 2


def test_missing_actor_is_rejected(db: Session):
    _org, actor, _deal, _ws = _org_deal(db, "noactor")
    with pytest.raises(inbox.ReviewInboxError):
        inbox.my_reviews(db, actor.organization_id, None)
