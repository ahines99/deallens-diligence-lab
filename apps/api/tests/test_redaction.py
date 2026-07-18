"""G75 — four-eyes data-room redaction workflow.

Offline coverage for the flagship trust item: span proposals validated against real immutable
chunks, four-eyes human-only decisions (proposer excluded, trusted-service banned on both
sides), approval minting a NEW immutable redacted version through the standard supersession
path (originals byte-identical forever), latest-version-wins routing into data-room QA and
cross-corpus QA with the fixed ``[REDACTED]`` marker never leaking the original text, and
stale-version refusal once a version has been superseded.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.session import get_session
from src.models.deal_intelligence import DataRoomDocument
from src.models.workspace import Workspace
from src.routers.deal_intelligence import router as intelligence_router
from src.routers.deal_workflow import router as workflow_router
from src.schemas.deal_intelligence import (
    CitedQARequest,
    DocumentTextCreate,
    RedactionDecisionRequest,
    RedactionProposalCreate,
    RedactionSpanIn,
)
from src.schemas.deal_workflow import (
    ActorContext,
    DealCreate,
    FundCreate,
    OrganizationCreate,
)
from src.services import cross_corpus_qa_service
from src.services import deal_intelligence_service as service
from src.services import deal_workflow_service as workflow

_PUBLIC_LINE = "FY2025 adjusted EBITDA was $38.25 million per the quality of earnings review."
_SECRET_LINE = (
    "The founder settlement cost $4.5 million and remains strictly confidential per counsel."
)
_SECRET_PHRASE = "$4.5 million"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _setup(db: Session, suffix: str, *, workspace_id: str | None = None):
    creator = ActorContext(actor_id=f"proposer-{suffix}", display_name="Deal Lead")
    organization = workflow.create_organization(
        db,
        OrganizationCreate(name=f"Redaction Org {suffix}", slug=f"redaction-{suffix}"),
        creator,
    )
    proposer = creator.model_copy(update={"organization_id": organization.id})
    approver = ActorContext(
        actor_id=f"approver-{suffix}",
        display_name="Compliance Partner",
        organization_id=organization.id,
    )
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), proposer)
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(
            code=f"RX-{suffix}",
            name=f"Project {suffix}",
            target_company="Redaction Target",
            workspace_id=workspace_id,
        ),
        proposer,
    )
    return proposer, approver, deal


def _ingest_two_paragraphs(db: Session, deal_id: str, actor: ActorContext) -> DataRoomDocument:
    return service.ingest_text_document(
        db,
        deal_id,
        DocumentTextCreate(
            filename="Settlement Memo.txt",
            title="Settlement Memo",
            text=f"{_PUBLIC_LINE}\n\n{_SECRET_LINE}",
            document_metadata={"workstream": "legal"},
        ),
        actor,
    )


def _secret_span(db: Session, actor: ActorContext, document_id: str) -> RedactionSpanIn:
    chunks = service.list_chunks(db, document_id, actor)
    secret_chunk = next(chunk for chunk in chunks if _SECRET_PHRASE in chunk.text)
    start = secret_chunk.text.index(_SECRET_PHRASE)
    return RedactionSpanIn(
        chunk_id=secret_chunk.id,
        start=start,
        end=start + len(_SECRET_PHRASE),
        reason="settlement amount is privileged",
    )


def test_span_validation_rejects_bad_chunk_out_of_range_and_overlap(db: Session):
    proposer, _, deal = _setup(db, "spans")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    other = service.ingest_text_document(
        db,
        deal.id,
        DocumentTextCreate(filename="other.txt", text="An unrelated document."),
        proposer,
    )
    other_chunk = service.list_chunks(db, other.id, proposer)[0]
    valid = _secret_span(db, proposer, document.id)

    with pytest.raises(service.IntelligenceError, match="not a chunk of document") as bad_chunk:
        service.propose_redaction(
            db,
            deal.id,
            document.id,
            RedactionProposalCreate(
                spans=[valid.model_copy(update={"chunk_id": other_chunk.id})]
            ),
            proposer,
        )
    assert bad_chunk.value.status_code == 422

    with pytest.raises(service.IntelligenceError, match="exceeds") as out_of_range:
        service.propose_redaction(
            db,
            deal.id,
            document.id,
            RedactionProposalCreate(spans=[valid.model_copy(update={"end": 100_000})]),
            proposer,
        )
    assert out_of_range.value.status_code == 422

    with pytest.raises(service.IntelligenceError, match="overlap") as overlapping:
        service.propose_redaction(
            db,
            deal.id,
            document.id,
            RedactionProposalCreate(
                spans=[valid, valid.model_copy(update={"start": valid.start + 1})]
            ),
            proposer,
        )
    assert overlapping.value.status_code == 422

    # An inverted span never reaches the service: the schema refuses it.
    with pytest.raises(ValueError, match="greater than span start"):
        RedactionSpanIn(chunk_id=other_chunk.id, start=5, end=5)


def test_four_eyes_proposer_trusted_service_and_anonymous_are_refused(db: Session):
    proposer, approver, deal = _setup(db, "foureyes")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    span = _secret_span(db, proposer, document.id)
    request = RedactionProposalCreate(spans=[span], note="redact the amount")
    automation = proposer.model_copy(
        update={"actor_id": "automation-bot", "via_trusted_service": True}
    )

    with pytest.raises(service.IntelligenceError) as anonymous:
        service.propose_redaction(db, deal.id, document.id, request, None)
    assert anonymous.value.status_code == 401

    with pytest.raises(service.IntelligenceError, match="human user session") as robot_propose:
        service.propose_redaction(db, deal.id, document.id, request, automation)
    assert robot_propose.value.status_code == 403

    proposal = service.propose_redaction(db, deal.id, document.id, request, proposer)
    assert proposal.status == "proposed"
    assert proposal.proposed_by_actor_id == proposer.actor_id
    assert proposal.document_version == 1

    decision = RedactionDecisionRequest(decision="approve", note="reviewed")
    with pytest.raises(service.IntelligenceConflict, match="distinct reviewer"):
        service.decide_redaction(db, proposal.id, decision, proposer)

    with pytest.raises(service.IntelligenceError, match="human user session") as robot_decide:
        service.decide_redaction(db, proposal.id, decision, automation)
    assert robot_decide.value.status_code == 403

    decided, redacted = service.decide_redaction(db, proposal.id, decision, approver)
    assert decided.status == "approved"
    assert decided.decided_by_actor_id == approver.actor_id
    assert decided.decided_at is not None
    assert redacted is not None and decided.redacted_document_id == redacted.id

    # A decided proposal is final — four-eyes decisions cannot be replayed or flipped.
    with pytest.raises(service.IntelligenceConflict, match="already approved"):
        service.decide_redaction(
            db, proposal.id, RedactionDecisionRequest(decision="reject"), approver
        )


def test_approval_mints_redacted_next_version_and_original_stays_byte_identical(db: Session):
    proposer, approver, deal = _setup(db, "mint")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    original_bytes = bytes(document.raw_bytes)
    original_sha = document.sha256
    original_chunk_texts = [c.text for c in service.list_chunks(db, document.id, proposer)]

    proposal = service.propose_redaction(
        db,
        deal.id,
        document.id,
        RedactionProposalCreate(spans=[_secret_span(db, proposer, document.id)]),
        proposer,
    )
    _, redacted = service.decide_redaction(
        db, proposal.id, RedactionDecisionRequest(decision="approve"), approver
    )

    assert redacted is not None
    assert redacted.version == document.version + 1
    assert redacted.logical_document_id == document.logical_document_id
    assert redacted.supersedes_document_id == document.id
    assert redacted.extension == ".txt"
    assert redacted.filename == "Settlement Memo.txt.redacted.txt"
    assert redacted.document_metadata["redaction_of"] == document.id
    assert redacted.document_metadata["proposal_id"] == proposal.id
    assert redacted.document_metadata["spans_count"] == 1
    # Original metadata carries over so metadata-filtered reads keep matching.
    assert redacted.document_metadata["workstream"] == "legal"

    expected = _SECRET_LINE.replace(_SECRET_PHRASE, service.REDACTION_MARKER)
    redacted_texts = [c.text for c in service.list_chunks(db, redacted.id, proposer)]
    assert redacted_texts == [_PUBLIC_LINE, expected]
    assert all(_SECRET_PHRASE not in text for text in redacted_texts)

    # The original version is untouched: bytes, hash, and chunks are byte-identical, and the
    # privileged version history still serves it.
    db.expire_all()
    persisted = service.get_document(db, document.id, proposer)
    assert bytes(persisted.raw_bytes) == original_bytes
    assert persisted.sha256 == original_sha
    assert [
        c.text for c in service.list_chunks(db, document.id, proposer)
    ] == original_chunk_texts
    versions = service.list_document_versions(
        db, deal.id, document.logical_document_id, proposer
    )
    assert [item.version for item in versions] == [1, 2]

    # Latest-version-wins routing: the default document list and data-room QA now serve the
    # redacted version with no special-casing, and the marker replaces the original text.
    assert service.list_documents(db, deal.id, proposer)[0].id == redacted.id
    answer = service.answer_question(
        db,
        deal.id,
        CitedQARequest(question="What did the founder settlement cost?"),
        proposer,
    )
    assert answer.status == "answered"
    assert service.REDACTION_MARKER in answer.answer
    assert _SECRET_PHRASE not in answer.answer
    assert all(_SECRET_PHRASE not in c["quote"] for c in answer.citations)
    assert answer.citations[0]["document_id"] == redacted.id
    assert answer.citations[0]["document_version"] == 2


def test_cross_corpus_qa_serves_redacted_latest_and_never_leaks_original(db: Session):
    workspace = Workspace(name="Redaction WS", deal_type="buyout", status="draft")
    db.add(workspace)
    db.commit()
    proposer, approver, deal = _setup(db, "xcorpus", workspace_id=workspace.id)
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    proposal = service.propose_redaction(
        db,
        deal.id,
        document.id,
        RedactionProposalCreate(spans=[_secret_span(db, proposer, document.id)]),
        proposer,
    )
    service.decide_redaction(
        db, proposal.id, RedactionDecisionRequest(decision="approve"), approver
    )

    result = cross_corpus_qa_service.answer(
        db, workspace.id, "What did the founder settlement cost the company?"
    )
    assert result["deal_id"] == deal.id
    assert result["status"] in {"answered", "partial"}
    assert service.REDACTION_MARKER in result["answer"]
    assert _SECRET_PHRASE not in result["answer"]
    for citation in result["citations"]:
        assert citation["corpus"] == "confidential_dataroom"
        assert _SECRET_PHRASE not in citation["quote"]
        assert citation["provenance"]["document_version"] == 2


def test_rejected_proposal_mints_nothing_and_is_final(db: Session):
    proposer, approver, deal = _setup(db, "reject")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    proposal = service.propose_redaction(
        db,
        deal.id,
        document.id,
        RedactionProposalCreate(spans=[_secret_span(db, proposer, document.id)]),
        proposer,
    )
    decided, redacted = service.decide_redaction(
        db,
        proposal.id,
        RedactionDecisionRequest(decision="reject", note="not privileged"),
        approver,
    )
    assert redacted is None
    assert decided.status == "rejected"
    assert decided.decision_note == "not privileged"
    assert decided.redacted_document_id is None
    versions = service.list_document_versions(
        db, deal.id, document.logical_document_id, proposer
    )
    assert [item.version for item in versions] == [1]
    with pytest.raises(service.IntelligenceConflict, match="already rejected"):
        service.decide_redaction(
            db, proposal.id, RedactionDecisionRequest(decision="approve"), approver
        )


def test_superseded_version_gets_stale_conflict_on_propose_and_approve(db: Session):
    proposer, approver, deal = _setup(db, "stale")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    span = _secret_span(db, proposer, document.id)
    first = service.propose_redaction(
        db, deal.id, document.id, RedactionProposalCreate(spans=[span]), proposer
    )
    second = service.propose_redaction(
        db, deal.id, document.id, RedactionProposalCreate(spans=[span]), proposer
    )
    service.decide_redaction(
        db, first.id, RedactionDecisionRequest(decision="approve"), approver
    )

    # A new proposal against the now-superseded version is refused with a stale 409.
    with pytest.raises(service.IntelligenceConflict, match="stale") as stale_propose:
        service.propose_redaction(
            db, deal.id, document.id, RedactionProposalCreate(spans=[span]), proposer
        )
    assert stale_propose.value.status_code == 409

    # A pending proposal authored against the superseded version can no longer be approved
    # (its offsets address text the data room no longer serves) — but rejecting stays safe.
    with pytest.raises(service.IntelligenceConflict, match="stale"):
        service.decide_redaction(
            db, second.id, RedactionDecisionRequest(decision="approve"), approver
        )
    decided, minted = service.decide_redaction(
        db, second.id, RedactionDecisionRequest(decision="reject"), approver
    )
    assert decided.status == "rejected" and minted is None


def test_decided_proposals_are_append_only_at_the_orm_layer(db: Session):
    proposer, approver, deal = _setup(db, "immutable")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    proposal = service.propose_redaction(
        db,
        deal.id,
        document.id,
        RedactionProposalCreate(spans=[_secret_span(db, proposer, document.id)]),
        proposer,
    )
    service.decide_redaction(
        db, proposal.id, RedactionDecisionRequest(decision="approve"), approver
    )

    proposal.spans = [{"chunk_id": "forged", "start": 0, "end": 1, "reason": ""}]
    with pytest.raises(ValueError, match="final"):
        db.flush()
    db.rollback()

    proposal.status = "proposed"
    with pytest.raises(ValueError, match="final"):
        db.flush()
    db.rollback()


def test_bulk_core_statements_cannot_mutate_immutable_intelligence_tables(db: Session):
    """LOW-1 regression: mapper-level guards only see unit-of-work flushes; a session-executed
    Core update/delete must be rejected too (mirroring the sibling modules' guards)."""
    from sqlalchemy import delete, update

    from src.models.deal_intelligence import DataRoomChunk, RedactionProposal

    proposer, _approver, deal = _setup(db, "bulkguard")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    proposal = service.propose_redaction(
        db,
        deal.id,
        document.id,
        RedactionProposalCreate(spans=[_secret_span(db, proposer, document.id)]),
        proposer,
    )
    with pytest.raises(ValueError, match="append-only"):
        db.execute(
            update(RedactionProposal)
            .where(RedactionProposal.id == proposal.id)
            .values(status="approved")
        )
    db.rollback()
    with pytest.raises(ValueError, match="append-only"):
        db.execute(delete(DataRoomChunk).where(DataRoomChunk.document_id == document.id))
    db.rollback()
    assert db.get(RedactionProposal, proposal.id).status == "proposed"


def test_list_redactions_filters_by_status(db: Session):
    proposer, approver, deal = _setup(db, "listing")
    document = _ingest_two_paragraphs(db, deal.id, proposer)
    span = _secret_span(db, proposer, document.id)
    kept = service.propose_redaction(
        db, deal.id, document.id, RedactionProposalCreate(spans=[span]), proposer
    )
    rejected = service.propose_redaction(
        db, deal.id, document.id, RedactionProposalCreate(spans=[span]), proposer
    )
    service.decide_redaction(
        db, rejected.id, RedactionDecisionRequest(decision="reject"), approver
    )

    assert {item.id for item in service.list_redactions(db, deal.id, proposer)} == {
        kept.id,
        rejected.id,
    }
    assert [
        item.id for item in service.list_redactions(db, deal.id, proposer, status="proposed")
    ] == [kept.id]
    assert [
        item.id for item in service.list_redactions(db, deal.id, proposer, status="rejected")
    ] == [rejected.id]
    with pytest.raises(service.IntelligenceError) as invalid:
        service.list_redactions(db, deal.id, proposer, status="bogus")
    assert invalid.value.status_code == 422


def test_router_propose_decide_and_list_contract():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(workflow_router)
    app.include_router(intelligence_router)

    def session_override():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        organization = client.post(
            "/api/organizations", json={"name": "Redaction Router Org", "slug": "redaction-router"}
        ).json()
        proposer_headers = {
            "X-Actor-ID": "router-proposer",
            "X-Organization-ID": organization["id"],
        }
        approver_headers = {
            "X-Actor-ID": "router-approver",
            "X-Organization-ID": organization["id"],
        }
        fund = client.post(
            f"/api/organizations/{organization['id']}/funds",
            json={"name": "Router Fund"},
            headers=proposer_headers,
        ).json()
        deal = client.post(
            f"/api/funds/{fund['id']}/deals",
            json={"code": "RXR-1", "name": "Router Redaction", "target_company": "Target"},
            headers=proposer_headers,
        ).json()
        document = client.post(
            f"/api/deals/{deal['id']}/intelligence/documents",
            json={"filename": "memo.txt", "text": _SECRET_LINE},
            headers=proposer_headers,
        ).json()
        chunk = client.get(
            f"/api/intelligence/documents/{document['id']}/chunks",
            headers=proposer_headers,
        ).json()[0]
        start = chunk["text"].index(_SECRET_PHRASE)
        span = {
            "chunk_id": chunk["id"],
            "start": start,
            "end": start + len(_SECRET_PHRASE),
            "reason": "privileged amount",
        }

        created = client.post(
            f"/api/deals/{deal['id']}/intelligence/documents/{document['id']}/redactions",
            json={"spans": [span], "note": "please redact"},
            headers=proposer_headers,
        )
        assert created.status_code == 201, created.text
        proposal = created.json()
        assert proposal["status"] == "proposed"
        assert proposal["spans"] == [span]
        assert proposal["document_version"] == 1

        self_decide = client.post(
            f"/api/intelligence/redactions/{proposal['id']}/decide",
            json={"decision": "approve"},
            headers=proposer_headers,
        )
        assert self_decide.status_code == 409

        decided = client.post(
            f"/api/intelligence/redactions/{proposal['id']}/decide",
            json={"decision": "approve", "note": "looks right"},
            headers=approver_headers,
        )
        assert decided.status_code == 200, decided.text
        body = decided.json()
        assert body["proposal"]["status"] == "approved"
        assert body["proposal"]["decided_by_actor_id"] == "router-approver"
        redacted = body["redacted_document"]
        assert redacted["version"] == 2
        assert redacted["id"] == body["proposal"]["redacted_document_id"]

        redacted_chunks = client.get(
            f"/api/intelligence/documents/{redacted['id']}/chunks",
            headers=proposer_headers,
        ).json()
        assert all(_SECRET_PHRASE not in item["text"] for item in redacted_chunks)
        assert any("[REDACTED]" in item["text"] for item in redacted_chunks)

        listing = client.get(
            f"/api/deals/{deal['id']}/intelligence/redactions",
            params={"status": "approved"},
            headers=proposer_headers,
        )
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()] == [proposal["id"]]

        stale = client.post(
            f"/api/deals/{deal['id']}/intelligence/documents/{document['id']}/redactions",
            json={"spans": [span]},
            headers=proposer_headers,
        )
        assert stale.status_code == 409
        assert "stale" in stale.json()["detail"]
    engine.dispose()
