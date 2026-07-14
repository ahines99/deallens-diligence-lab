"""Focused coverage for governed evidence, IC assembly, and immutable approvals."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request

from src.agents import llm_provider
from src.agents.citation_auditor import CitationAuditor
from src.db.base import Base
from src.models.evidence import Evidence
from src.models.underwriting_model import UnderwritingCaseDecision, UnderwritingCaseVersion
from src.models.workspace import Workspace
from src.routers.deal_workflow import actor_context
from src.schemas.deal_intelligence import ClaimReviewRequest, DocumentTextCreate, ExtractionRequest
from src.schemas.deal_workflow import (
    ActorContext,
    DealCreate,
    ExportRequest,
    FundCreate,
    ICDecisionCreate,
    ICPacketCreate,
    LedgerEntryCreate,
    OrganizationCreate,
    StageGateResolve,
    StageTransitionCreate,
    TeamMemberCreate,
)
from src.schemas.identity import PrincipalContext
from src.schemas.underwriting_model import (
    UnderwritingAssumptions,
    UnderwritingCaseCreate,
    UnderwritingDecisionCreate,
)
from src.services import deal_intelligence_service as intelligence
from src.services import deal_workflow_service as workflow
from src.services import evidence_service
from src.services import underwriting_model_service as underwriting


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _governed_deal(db: Session):
    creator = ActorContext(actor_id="deal-lead", display_name="Deal Lead")
    organization = workflow.create_organization(
        db, OrganizationCreate(name="Governed Sponsor", slug="governed-sponsor"), creator
    )
    lead = creator.model_copy(update={"organization_id": organization.id})
    partner = ActorContext(
        actor_id="investment-partner",
        display_name="Investment Partner",
        organization_id=organization.id,
    )
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), lead)
    workspace = Workspace(name="Governed Underwrite", deal_type="buyout", status="draft")
    db.add(workspace)
    db.commit()
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(
            code="GOV-1",
            name="Project Governed",
            target_company="Governed Target",
            workspace_id=workspace.id,
        ),
        lead,
    )
    workflow.add_team_member(
        db,
        deal.id,
        TeamMemberCreate(
            actor_id=partner.actor_id or "investment-partner",
            display_name="Investment Partner",
            role="investment_partner",
        ),
        lead,
    )
    return lead, partner, workspace, deal


def _advance_to_ic(db: Session, deal_id: str, actor: ActorContext) -> None:
    stages = ["sourcing", "screening", "initial_review", "diligence"]
    destinations = ["screening", "initial_review", "diligence", "ic_review"]
    for stage, destination in zip(stages, destinations, strict=True):
        for gate in workflow.list_gates(db, deal_id, actor, stage):
            workflow.resolve_gate(
                db,
                gate.id,
                StageGateResolve(status="satisfied", resolution_note="Authenticated review"),
                actor,
            )
        workflow.transition_deal(
            db,
            deal_id,
            StageTransitionCreate(to_stage=destination, rationale="Approved to advance"),
            actor,
        )
    for gate in workflow.list_gates(db, deal_id, actor, "ic_review"):
        workflow.resolve_gate(
            db,
            gate.id,
            StageGateResolve(status="satisfied", resolution_note="IC materials checked"),
            actor,
        )


def _approved_case(db: Session, workspace_id: str) -> UnderwritingCaseVersion:
    case = UnderwritingCaseVersion(
        workspace_id=workspace_id,
        case_key="base",
        label="Base case",
        version=1,
        schema_version="1.0",
        assumptions={"entry_enterprise_value": 250_000_000, "exit_year": 5},
        result={"irr": 0.24, "moic": 2.7},
        input_hash="a" * 64,
        output_hash="b" * 64,
        created_by="model-analyst",
        change_note="Initial underwrite",
    )
    db.add(case)
    db.commit()
    db.add(
        UnderwritingCaseDecision(
            workspace_id=workspace_id,
            case_version_id=case.id,
            decision="submitted",
            actor="model-analyst",
            rationale="Submitted for independent review",
        )
    )
    db.commit()
    db.add(
        UnderwritingCaseDecision(
            workspace_id=workspace_id,
            case_version_id=case.id,
            decision="approved",
            actor="investment-partner",
            rationale="Model tie-out complete",
        )
    )
    db.commit()
    return case


def test_governed_ic_packet_is_server_assembled_from_approved_sources(db: Session):
    lead, partner, workspace, deal = _governed_deal(db)
    case = _approved_case(db, workspace.id)
    document = intelligence.ingest_text_document(
        db,
        deal.id,
        DocumentTextCreate(
            filename="qoe.txt",
            text="Management proposed a one-time $3 million EBITDA add-back.",
        ),
        lead,
    )
    claim = next(
        item
        for item in intelligence.extract_structured_claims(
            db,
            deal.id,
            ExtractionRequest(document_ids=[document.id], categories=["qoe_candidate"]),
            lead,
        )
        if item.category == "qoe_candidate"
    )
    with pytest.raises(intelligence.IntelligenceConflict, match="distinct reviewer"):
        intelligence.review_claim(
            db, claim.id, ClaimReviewRequest(action="approve", expected_revision=1), lead
        )
    approved_claim, _ = intelligence.review_claim(
        db,
        claim.id,
        ClaimReviewRequest(
            action="approve", expected_revision=1, note="QoE workpaper reconciled"
        ),
        partner,
    )
    promoted = db.scalar(
        select(Evidence).where(
            Evidence.workspace_id == workspace.id,
            Evidence.source_type == "approved_private_claim",
        )
    )
    assert promoted is not None
    binding = json.loads(promoted.source_section or "{}")
    assert binding["claim_id"] == approved_claim.id
    assert binding["document_id"] == document.id
    assert binding["chunk_id"] == approved_claim.chunk_id
    case = underwriting.create_case_version(
        db,
        workspace.id,
        UnderwritingCaseCreate(
            case_key="base",
            label="Base case with approved QoE evidence",
            assumptions=UnderwritingAssumptions.model_validate(
                {
                    "historical": {"ltm_revenue": 100.0, "ltm_ebitda": 20.0},
                    "transaction": {
                        "close_date": "2026-01-01",
                        "entry_multiple": 10.0,
                        "exit_multiple": 10.0,
                    },
                }
            ),
            approved_claim_ids=[approved_claim.id],
            expected_parent_version=1,
            created_by=lead.actor_id or "deal-lead",
            change_note="Bind independently approved QoE evidence into the model input",
        ),
        lead,
    )
    underwriting.add_case_decision(
        db,
        workspace.id,
        "base",
        case.version,
        UnderwritingDecisionCreate(
            decision="submitted",
            actor=lead.actor_id or "deal-lead",
            rationale="Submit evidence-bound model",
        ),
    )
    underwriting.add_case_decision(
        db,
        workspace.id,
        "base",
        case.version,
        UnderwritingDecisionCreate(
            decision="approved",
            actor=partner.actor_id or "investment-partner",
            rationale="Approve evidence-bound model",
        ),
    )
    assert case.approved_claim_ids == [approved_claim.id]
    assert case.approved_claim_manifest[0]["claim_id"] == approved_claim.id
    assert len(case.claim_manifest_hash) == 64
    evidence = evidence_service.create(
        db,
        workspace.id,
        claim="Reported revenue reconciles to the audited statements.",
        claim_type="fact",
        source_name="Audited financial statements",
        source_type="financial_statement",
        evidence_text="FY2025 revenue was $100 million.",
        confidence=0.99,
        agent_name="financial_analyst",
    )
    db.commit()
    workflow.create_ledger_entry(
        db,
        deal.id,
        LedgerEntryCreate(
            entry_type="thesis",
            title="Durable cash generation",
            description="Recurring revenue supports deleveraging.",
            status="validated",
            evidence_refs=[evidence.ref],
        ),
        lead,
    )
    _advance_to_ic(db, deal.id, lead)

    with pytest.raises(ValidationError, match="does not accept client-owned snapshots"):
        ICPacketCreate(
            title="Untrusted packet",
            case_version_ids=[case.id],
            scenario_snapshot={"irr": 0.99},
            decision_request={"ask": "Approve"},
        )

    packet = workflow.create_ic_packet(
        db,
        deal.id,
        ICPacketCreate(
            title="Governed IC Memorandum",
            case_version_ids=[case.id],
            workspace_evidence_refs=[evidence.ref],
            decision_request={"ask": "Approve signing authority"},
        ),
        lead,
    )
    assert packet.scenario_snapshot["_assembly"]["mode"] == "governed"
    assert packet.scenario_snapshot["_assembly"]["case_bound_approved_claim_ids"] == [
        approved_claim.id
    ]
    assert packet.scenario_snapshot["cases"][0]["assumptions"] == case.assumptions
    assert packet.model_snapshot["cases"][0]["result"] == case.result
    assert {item["kind"] for item in packet.evidence_manifest} == {
        "workspace_evidence",
        "approved_private_claim",
    }
    private_entry = next(
        item for item in packet.evidence_manifest if item["kind"] == "approved_private_claim"
    )
    assert private_entry["claim_id"] == approved_claim.id
    assert private_entry["source"]["document_id"] == document.id
    assert private_entry["source"]["chunk_id"] == approved_claim.chunk_id
    assert private_entry["source"]["document_sha256"] == document.sha256
    assert private_entry["governed_evidence"]["evidence_id"] == promoted.id
    assert private_entry["governed_evidence"]["ref"] == promoted.ref
    assert packet.thesis_snapshot[0]["title"] == "Durable cash generation"

    readiness = workflow.evaluate_ic_readiness(db, packet.id, lead)
    assert readiness["ready"] is True
    assert next(
        item for item in readiness["checks"] if item["code"] == "approved_case_versions"
    )["passed"] is True
    with pytest.raises(workflow.WorkflowError) as missing_actor:
        workflow.submit_ic_packet(db, packet.id, ActorContext(organization_id=lead.organization_id))
    assert missing_actor.value.status_code == 401
    workflow.submit_ic_packet(db, packet.id, lead)
    with pytest.raises(workflow.WorkflowConflict, match="submitter"):
        workflow.record_ic_decision(
            db,
            packet.id,
            ICDecisionCreate(decision="approve", rationale="Self approval"),
            lead,
        )
    decision, _ = workflow.record_ic_decision(
        db,
        packet.id,
        ICDecisionCreate(decision="approve", rationale="Independent IC approval"),
        partner,
    )
    assert decision.decided_by_actor_id == partner.actor_id

    export = workflow.create_export_manifest(
        db, packet.id, ExportRequest(format="json"), partner
    )
    verified = workflow.verify_export_manifest(db, export.id, partner)
    assert verified["valid"] is True, [
        item for item in verified["checks"] if not item["passed"]
    ]
    assert {item["code"] for item in verified["checks"]} == {
        "canonical_manifest_hash",
        "packet_binding",
        "packet_content_binding",
        "section_bindings",
        "evidence_bindings",
        "case_bindings",
    }
    assert all(item["passed"] for item in verified["checks"])

    # Even if a database attacker rewrites the stored digest too, the verifier catches the
    # manifest's broken binding to the immutable packet content.
    tampered_manifest = deepcopy(export.manifest)
    tampered_manifest["packet"]["content_hash"] = "0" * 64
    export.manifest = tampered_manifest
    export.manifest_hash = workflow._sha256(tampered_manifest)
    db.commit()
    tampered = workflow.verify_export_manifest(db, export.id, partner)
    checks = {item["code"]: item for item in tampered["checks"]}
    assert tampered["valid"] is False
    assert checks["canonical_manifest_hash"]["passed"] is True
    assert checks["packet_content_binding"]["passed"] is False

    stale_packet = workflow.create_ic_packet(
        db,
        deal.id,
        ICPacketCreate(
            title="Governed IC Memorandum - refreshed",
            case_version_ids=[case.id],
            approved_claim_ids=[approved_claim.id],
            workspace_evidence_refs=[evidence.ref],
            decision_request={"ask": "Approve refreshed materials"},
        ),
        lead,
    )
    intelligence.review_claim(
        db,
        approved_claim.id,
        ClaimReviewRequest(
            action="edit",
            expected_revision=2,
            value_text="Management proposed a revised $2 million EBITDA add-back.",
        ),
        lead,
    )
    stale_readiness = workflow.evaluate_ic_readiness(db, stale_packet.id, lead)
    source_check = next(
        item
        for item in stale_readiness["checks"]
        if item["code"] == "governed_sources_current"
    )
    assert source_check["passed"] is False
    assert approved_claim.id in source_check["entity_ids"]


def test_evidence_refs_retry_monotonically_and_cannot_be_cleared(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    workspace = Workspace(name="Evidence", deal_type="buyout", status="draft")
    db.add(workspace)
    db.commit()

    def create_claim(label: str):
        return evidence_service.create(
            db,
            workspace.id,
            claim=label,
            claim_type="fact",
            source_name="Source",
            source_type="test",
            evidence_text=label,
            confidence=0.9,
            agent_name="test",
        )

    first = create_claim("First")
    db.commit()
    assert first.ref == "EV-001"
    original_next_ref = evidence_service.next_ref
    calls = 0

    def colliding_then_current(session: Session, workspace_id: str) -> str:
        nonlocal calls
        calls += 1
        return "EV-001" if calls == 1 else original_next_ref(session, workspace_id)

    monkeypatch.setattr(evidence_service, "next_ref", colliding_then_current)
    second = create_claim("Second")
    db.commit()
    assert second.ref == "EV-002"
    assert calls == 2

    db.add(
        Evidence(
            workspace_id=workspace.id,
            ref="EV-010",
            claim="Reserved",
            claim_type="fact",
            source_name="Source",
            source_type="test",
            evidence_text="Reserved",
            confidence=0.9,
            agent_name="test",
        )
    )
    db.commit()
    assert original_next_ref(db, workspace.id) == "EV-011"
    with pytest.raises(ValueError, match="append-only"):
        evidence_service.clear(db, workspace.id)
    duplicate = Evidence(
        workspace_id=workspace.id,
        ref="EV-010",
        claim="Duplicate",
        claim_type="fact",
        source_name="Source",
        source_type="test",
        evidence_text="Duplicate",
        confidence=0.9,
        agent_name="test",
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    first_row = db.get(Evidence, first.id)
    assert first_row is not None
    first_row.claim = "Tampered claim"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    with pytest.raises(ValueError, match="append-only"):
        db.execute(delete(Evidence).where(Evidence.id == first.id))
    db.rollback()


def test_underwriting_approval_requires_two_actors_and_bulk_delete_is_blocked(db: Session):
    workspace = Workspace(name="Immutable Model", deal_type="buyout", status="draft")
    db.add(workspace)
    db.commit()
    case = _approved_case(db, workspace.id)
    with pytest.raises(ValueError, match="append-only"):
        db.execute(
            delete(UnderwritingCaseVersion).where(UnderwritingCaseVersion.id == case.id)
        )
    db.rollback()

    second = UnderwritingCaseVersion(
        workspace_id=workspace.id,
        case_key="downside",
        label="Downside",
        version=1,
        assumptions={},
        result={},
        input_hash="c" * 64,
        output_hash="d" * 64,
        created_by="same-actor",
    )
    db.add(second)
    db.commit()
    db.add(
        UnderwritingCaseDecision(
            workspace_id=workspace.id,
            case_version_id=second.id,
            decision="submitted",
            actor="same-actor",
        )
    )
    db.commit()
    db.add(
        UnderwritingCaseDecision(
            workspace_id=workspace.id,
            case_version_id=second.id,
            decision="approved",
            actor="same-actor",
        )
    )
    with pytest.raises(ValueError, match="submitter cannot approve"):
        db.commit()
    db.rollback()


def test_llm_rewrite_fails_closed_on_numeric_or_citation_drift(
    monkeypatch: pytest.MonkeyPatch,
):
    source = "Revenue was $100 million in FY2025 [EV-001]. Margin was 20% [EV-002]."
    safe = "In FY2025, revenue was $100 million [EV-001]. The margin was 20% [EV-002]."
    assert CitationAuditor.audit_rewrite(source, safe).faithful is True
    numeric_drift = source.replace("$100 million", "$110 million")
    audit = CitationAuditor.audit_rewrite(source, numeric_drift)
    assert audit.faithful is False
    assert audit.numeric_tokens_added == ("$110million",)
    citation_drift = source.replace("[EV-002]", "[EV-003]")
    assert CitationAuditor.audit_rewrite(source, citation_drift).citation_sequence_changed is True
    reassigned = "Margin was 20% in FY2025 [EV-001]. Revenue was $100 million [EV-002]."
    assert CitationAuditor.audit_rewrite(
        source, reassigned
    ).citation_numeric_context_changed is True

    monkeypatch.setattr(llm_provider.settings, "llm_mode", "live")
    monkeypatch.setattr(llm_provider.settings, "llm_api_key", "test-key")
    calls: list[str] = []

    def record_call(self, system, user):
        calls.append(user)
        return safe

    monkeypatch.setattr(llm_provider.LiveProvider, "complete", record_call)
    assert llm_provider.polish_markdown(source) == source
    assert calls == []
    monkeypatch.setattr(
        llm_provider.LiveProvider,
        "complete",
        lambda self, system, user: numeric_drift,
    )
    assert llm_provider.polish_markdown(source, external_allowed=True) == source
    monkeypatch.setattr(
        llm_provider.LiveProvider,
        "complete",
        lambda self, system, user: safe,
    )
    assert llm_provider.polish_markdown(source, external_allowed=True) == safe


def test_verified_principal_overrides_spoofed_actor_headers():
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.principal = PrincipalContext(
        user_id="verified-user",
        session_id="session-1",
        email="verified@example.com",
        display_name="Verified User",
        organization_id="a" * 32,
        membership_id="membership-1",
        role="admin",
    )
    actor = actor_context(
        request,
        header_actor_id="spoofed-user",
        header_display_name="Spoofed",
        header_organization_id="b" * 32,
        header_roles="observer",
        header_request_id="request-1",
    )
    assert actor.actor_id == "verified-user"
    assert actor.display_name == "Verified User"
    assert actor.organization_id == "a" * 32
    assert "organization_admin" in actor.roles
