"""G60 — agent proposals into the four-eyes queues, driven through the governed tool registry.

The boundary these tests pin: the agent may PROPOSE (QoE adjustments, structured claims) under
the distinguishable identity ``agent:diligence``; an agent-proposed item can NEVER be decided
by automation — the proposer!=decider rules and the trusted-service reviewer ban provably
reject it — while a human deciding it stays possible and unchanged. Unverifiable claim
proposals are tool errors and mint nothing.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.base import Base
from src.models.deal_intelligence import StructuredClaim
from src.models.underwriting_data import QoEAdjustment
from src.models.workspace import Workspace
from src.schemas.deal_intelligence import ClaimReviewRequest, DocumentTextCreate
from src.schemas.deal_workflow import (
    ActorContext,
    DealCreate,
    FundCreate,
    OrganizationCreate,
)
from src.schemas.underwriting_data import PrivateTargetCreate, QoEAdjustmentDecision
from src.services import agent_tools
from src.services import deal_intelligence_service as intelligence
from src.services import deal_workflow_service as workflow
from src.services import review_inbox_service as inbox
from src.services import underwriting_data_service as underwriting

AGENT = agent_tools.AGENT_ACTOR_ID
HUMAN_PROPOSER = "deal-lead"
HUMAN_REVIEWER = "investment-partner"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _org_deal(db: Session, suffix: str):
    """Org + fund + deal bound to a workspace with a private target; returns actors too."""
    creator = ActorContext(actor_id=HUMAN_PROPOSER, display_name="Deal Lead")
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"Agent Org {suffix}", slug=f"agent-org-{suffix}"), creator
    )
    lead = creator.model_copy(update={"organization_id": organization.id})
    reviewer = ActorContext(
        actor_id=HUMAN_REVIEWER,
        display_name="Investment Partner",
        organization_id=organization.id,
    )
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), lead)
    workspace = Workspace(name=f"Agent WS {suffix}", deal_type="buyout", status="draft")
    db.add(workspace)
    db.flush()
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(
            code=f"AGT-{suffix}",
            name=f"Project Agent {suffix}",
            target_company="Agent Target Co",
            workspace_id=workspace.id,
        ),
        lead,
    )
    underwriting.create_private_target(
        db, workspace.id, PrivateTargetCreate(name=f"Agent Target {suffix}")
    )
    return organization, lead, reviewer, deal, workspace


def _run_tool(db: Session, workspace_id: str, name: str, arguments: dict):
    return agent_tools._execute_tool(db, workspace_id, name, arguments)


def _claim_ids(db: Session) -> set[str]:
    return set(db.scalars(select(StructuredClaim.id)))


# --- QoE plane --------------------------------------------------------------------------------


def test_agent_proposed_qoe_lands_in_inbox_and_only_a_distinct_human_can_decide(db: Session):
    organization, _lead, _reviewer, _deal, workspace = _org_deal(db, "qoe")
    ok, result = _run_tool(
        db,
        workspace.id,
        "propose_qoe_adjustment",
        {
            "category": "non_recurring",
            "description": "One-time legal settlement excluded from run-rate EBITDA.",
            "amount": 1_250_000,
            "period_end": "2025-12-31",
            "bridge_layer": "management",
            "evidence_ref": "EV-001",
            "source_note": "10-K Item 7 adjusted EBITDA reconciliation table",
        },
    )
    assert ok, result
    assert result["proposed"] is True
    assert result["status"] == "proposed"
    assert result["created_by"] == AGENT

    adjustment = db.get(QoEAdjustment, result["adjustment_id"])
    assert adjustment is not None
    assert adjustment.status == "proposed"
    # The record itself carries the distinguishable proposer identity + the agent's source note.
    assert adjustment.created_by == AGENT
    assert "Agent source note: 10-K Item 7" in adjustment.description

    # Inbox: visible to a human reviewer; the four-eyes filter hides it from the agent identity.
    human_view = inbox.my_reviews(db, organization.id, HUMAN_REVIEWER)
    assert adjustment.id in [i["id"] for i in human_view["items"] if i["plane"] == "qoe"]
    agent_view = inbox.my_reviews(db, organization.id, AGENT)
    assert agent_view["counts_by_plane"]["qoe"] == 0

    # The agent identity can never decide its own proposal (proposer == decider conflict).
    with pytest.raises(
        underwriting.UnderwritingDataConflict,
        match="proposer cannot approve or reject the same adjustment",
    ):
        underwriting.decide_qoe_adjustment(
            db,
            workspace.id,
            adjustment.id,
            QoEAdjustmentDecision(decision="approve", decided_by=AGENT, note="self-approval"),
        )

    # A DIFFERENT human decides it exactly as before — the approval path is unchanged.
    decided = underwriting.decide_qoe_adjustment(
        db,
        workspace.id,
        adjustment.id,
        QoEAdjustmentDecision(
            decision="approve", decided_by=HUMAN_REVIEWER, note="Workpaper reconciled"
        ),
    )
    assert decided.status == "approved"
    assert decided.decided_by == HUMAN_REVIEWER


def test_duplicate_qoe_proposal_is_a_tool_error_via_the_existing_dedupe_key(db: Session):
    _org, _lead, _reviewer, _deal, workspace = _org_deal(db, "dedupe")
    arguments = {
        "category": "owner_compensation",
        "description": "Owner salary normalization to market rate.",
        "amount": 300_000,
        "period_end": "2025-12-31",
        "bridge_layer": "sponsor",
    }
    ok, first = _run_tool(db, workspace.id, "propose_qoe_adjustment", arguments)
    assert ok, first
    ok, error = _run_tool(db, workspace.id, "propose_qoe_adjustment", arguments)
    assert ok is False
    assert "Duplicate QoE adjustment" in error


def test_qoe_proposal_validation_failure_is_a_tool_error_never_a_crash(db: Session):
    _org, _lead, _reviewer, _deal, workspace = _org_deal(db, "invalid")
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_qoe_adjustment",
        {
            "category": "non_recurring",
            "description": "Zero-amount proposals are rejected by the schema.",
            "amount": 0,
            "period_end": "2025-12-31",
            "bridge_layer": "management",
        },
    )
    assert ok is False
    assert "invalid QoE proposal" in error
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_qoe_adjustment",
        {
            "category": "non_recurring",
            "description": "Covenant is not a proposable layer for the agent.",
            "amount": 100,
            "period_end": "2025-12-31",
            "bridge_layer": "covenant",
        },
    )
    assert ok is False
    assert "bridge_layer" in error
    assert db.scalar(select(QoEAdjustment)) is None


# --- Claim plane ------------------------------------------------------------------------------

_DOC_TEXT = "Management proposed a one-time $3 million EBITDA add-back."


def _ingest(db: Session, deal_id: str, actor: ActorContext):
    return intelligence.ingest_text_document(
        db, deal_id, DocumentTextCreate(filename="qoe.txt", text=_DOC_TEXT), actor
    )


def test_agent_proposed_claim_is_verified_minted_unreviewed_and_human_reviewable(db: Session):
    organization, lead, reviewer, deal, workspace = _org_deal(db, "claim")
    document = _ingest(db, deal.id, lead)
    chunk = intelligence.list_chunks(db, document.id)[0]

    ok, result = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "qoe_candidate",
            "field_name": "add_back",
            "value_text": "$3 million",
            "value_number": 3,
            "unit": "USD_millions",
            "period": "FY2025",
            "quote": "a one-time  $3 million EBITDA add-back",  # whitespace-normalized match
            "chunk_hint": "qoe.txt",
        },
    )
    assert ok, result
    assert result["proposed"] is True
    assert result["review_status"] == "unreviewed"
    assert result["created_by"] == AGENT
    assert result["extraction_version"] == "agent-proposed-v1"
    assert result["chunk_id"] == chunk.id
    assert result["locator"] == chunk.locator

    claim = db.get(StructuredClaim, result["claim_id"])
    assert claim is not None
    assert claim.review_status == "unreviewed"
    assert claim.created_by_actor_id == AGENT
    assert claim.extraction_version == "agent-proposed-v1"
    assert claim.source_locator == chunk.locator
    span = claim.source_span
    assert chunk.text[span["start"]:span["end"]] == span["text"]
    assert "$3 million" in span["text"]

    # Inbox: a human reviewer sees the agent-proposed claim; the agent identity never does.
    human_view = inbox.my_reviews(db, organization.id, HUMAN_REVIEWER)
    assert claim.id in [i["id"] for i in human_view["items"] if i["plane"] == "claim"]
    agent_view = inbox.my_reviews(db, organization.id, AGENT)
    assert agent_view["counts_by_plane"]["claim"] == 0

    # Automation can never approve it: the trusted-service reviewer ban holds for
    # agent-proposed items exactly as for human-proposed ones...
    service_actor = ActorContext(
        actor_id="automation-token",
        organization_id=organization.id,
        via_trusted_service=True,
    )
    with pytest.raises(intelligence.IntelligenceError, match="human user session"):
        intelligence.review_claim(
            db, claim.id, ClaimReviewRequest(action="approve", expected_revision=1), service_actor
        )
    # ...and an actor claiming the agent's own identity hits the proposer!=decider rule.
    with pytest.raises(intelligence.IntelligenceConflict, match="distinct reviewer"):
        intelligence.review_claim(
            db,
            claim.id,
            ClaimReviewRequest(action="approve", expected_revision=1),
            ActorContext(actor_id=AGENT, organization_id=organization.id),
        )

    # A human approves fine — unchanged four-eyes path, agent provenance preserved.
    approved, review = intelligence.review_claim(
        db,
        claim.id,
        ClaimReviewRequest(action="approve", expected_revision=1, note="Tie-out complete"),
        reviewer,
    )
    assert approved.review_status == "approved"
    assert review.reviewer_actor_id == HUMAN_REVIEWER
    assert approved.extraction_version == "agent-proposed-v1"

    # Same-span re-proposal after review does not mint a duplicate queue item.
    ok, dedupe = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "qoe_candidate",
            "field_name": "add_back",
            "value_text": "$3 million",
            "quote": "a one-time $3 million EBITDA add-back",
        },
    )
    assert ok and dedupe["proposed"] is False
    assert dedupe["claim_id"] == claim.id


def test_unverifiable_claim_proposals_are_tool_errors_and_mint_nothing(db: Session):
    _org, lead, _reviewer, deal, workspace = _org_deal(db, "unverif")
    _ingest(db, deal.id, lead)
    before = _claim_ids(db)

    # Fabricated quote: not present in any chunk.
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "qoe_candidate",
            "field_name": "add_back",
            "value_text": "$9 million",
            "quote": "a recurring $9 million synergy adjustment",
        },
    )
    assert ok is False and "quote_not_verbatim" in error

    # value_text not inside the (real) quote.
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "qoe_candidate",
            "field_name": "add_back",
            "value_text": "$4 million",
            "quote": "a one-time $3 million EBITDA add-back",
        },
    )
    assert ok is False and "value_text_not_in_quote" in error

    # value_number violating the digit-boundary rule: 30 never verifies against "$3 million".
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "qoe_candidate",
            "field_name": "add_back",
            "value_text": "$3 million",
            "value_number": 30,
            "quote": "a one-time $3 million EBITDA add-back",
        },
    )
    assert ok is False and "value_number_not_in_quote" in error

    # Category outside the claim taxonomy.
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "made_up_category",
            "field_name": "add_back",
            "value_text": "$3 million",
            "quote": "a one-time $3 million EBITDA add-back",
        },
    )
    assert ok is False and "category must be one of" in error

    assert _claim_ids(db) == before  # NOTHING was minted


def test_propose_claim_requires_a_deal_linked_workspace(db: Session):
    workspace = Workspace(name="Standalone WS", deal_type="buyout", status="draft")
    db.add(workspace)
    db.commit()
    ok, error = _run_tool(
        db,
        workspace.id,
        "propose_claim",
        {
            "category": "kpi",
            "field_name": "revenue",
            "value_text": "$120 million",
            "quote": "Revenue was $120 million.",
        },
    )
    assert ok is False
    assert "deal-linked workspaces" in error
    assert _claim_ids(db) == set()


# --- Registry contract ------------------------------------------------------------------------


def test_tool_definitions_declare_the_proposal_tools_alongside_the_read_registry():
    definitions = {tool["name"]: tool for tool in agent_tools.tool_definitions()}
    assert "search_filings" in definitions  # the pre-G60 read-only registry is intact

    qoe = definitions["propose_qoe_adjustment"]["input_schema"]
    assert qoe["required"] == ["category", "description", "amount", "period_end", "bridge_layer"]
    assert qoe["properties"]["bridge_layer"]["enum"] == ["management", "sponsor"]
    assert qoe["additionalProperties"] is False

    claim = definitions["propose_claim"]["input_schema"]
    assert claim["required"] == ["category", "field_name", "value_text", "quote"]
    assert claim["properties"]["category"]["enum"] == [
        "debt_term", "customer", "contract", "kpi", "qoe_candidate"
    ]
    assert claim["additionalProperties"] is False
