"""G53 — schema-constrained LLM claim extraction with locator verification.

The deal-intelligence extractor gains an LLM-first path: the model proposes structured claims
with supporting quotes, and a deterministic verifier mints an unreviewed ``StructuredClaim``
ONLY when the quote appears verbatim in a real chunk (whitespace-normalized, no case folding)
and the claimed value is visible inside the quote. Everything else — fabricated quotes,
paraphrases, absent values, mock mode, no consent, provider failure — either rejects the
proposal with a machine-readable reason or falls back to the unchanged pattern extractor.
No network: providers are fakes, exactly like ``test_structured_llm``.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models.deal_intelligence import StructuredClaim
from src.models.deal_workflow import WorkflowAuditEvent
from src.models.workspace import Workspace
from src.schemas.deal_intelligence import (
    ClaimReviewRequest,
    DocumentTextCreate,
    ExtractionRequest,
)
from src.schemas.deal_workflow import ActorContext, DealCreate, FundCreate, OrganizationCreate
from src.services import deal_intelligence_service as intelligence
from src.services import deal_workflow_service as workflow

_ARR_TEXT = "FY2025 ARR was $120 million."
_QOE_TEXT = "Management proposed a one-time $3 million EBITDA add-back."
_QOE_QUOTE = _QOE_TEXT  # the single-sentence paragraph IS the verbatim quote


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


class _FakeProvider:
    model = "fake-model"

    def __init__(self, response: str, *, raises: bool = False) -> None:
        self._response = response
        self._raises = raises
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if self._raises:
            raise RuntimeError("provider down")
        return self._response


def _payload(claims: list[dict]) -> str:
    return json.dumps({"claims": claims})


def _proposal(**overrides) -> dict:
    base = {
        "category": "qoe_candidate",
        "field_name": "add_back",
        "value_text": "$3 million",
        "value_number": 3,
        "unit": "USD_millions",
        "period": None,
        "quote": _QOE_QUOTE,
        "chunk_index": 1,
    }
    return {**base, **overrides}


def _llm_deal(db: Session, *, external_llm_allowed=True, data_classification="confidential"):
    creator = ActorContext(actor_id="claim-extractor", display_name="Claim Extractor")
    organization = workflow.create_organization(
        db, OrganizationCreate(name="LLM Claims Sponsor", slug="llm-claims-sponsor"), creator
    )
    lead = creator.model_copy(update={"organization_id": organization.id})
    partner = ActorContext(
        actor_id="claims-reviewer",
        display_name="Claims Reviewer",
        organization_id=organization.id,
    )
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), lead)
    workspace = Workspace(
        name="LLM Claims Underwrite",
        deal_type="buyout",
        status="draft",
        external_llm_allowed=external_llm_allowed,
        data_classification=data_classification,
    )
    db.add(workspace)
    db.commit()
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(
            code="LLM-1",
            name="Project LLM Claims",
            target_company="Claims Target",
            workspace_id=workspace.id,
        ),
        lead,
    )
    document = intelligence.ingest_text_document(
        db,
        deal.id,
        DocumentTextCreate(filename="qoe.txt", text=f"{_ARR_TEXT}\n\n{_QOE_TEXT}"),
        lead,
    )
    return lead, partner, deal, document


def _extract(db, deal, actor, provider, *, categories=("qoe_candidate",), document_ids=None):
    return intelligence.extract_structured_claims(
        db,
        deal.id,
        ExtractionRequest(document_ids=document_ids or [], categories=list(categories)),
        actor,
        provider_factory=lambda: provider,
    )


def _claim_rows(db: Session, deal_id: str) -> list[StructuredClaim]:
    return list(
        db.scalars(select(StructuredClaim).where(StructuredClaim.deal_id == deal_id))
    )


def _extraction_events(db: Session) -> list[WorkflowAuditEvent]:
    return list(
        db.scalars(
            select(WorkflowAuditEvent).where(
                WorkflowAuditEvent.action == "intelligence.claim_extraction"
            )
        )
    )


def test_verified_llm_claim_is_minted_unreviewed_and_four_eyes_still_holds(db, live_mode):
    lead, partner, deal, document = _llm_deal(db)
    chunks = intelligence.list_chunks(db, document.id, lead)
    provider = _FakeProvider(_payload([_proposal()]))

    claims = _extract(db, deal, lead, provider)
    assert provider.calls == 1
    assert len(claims) == 1
    claim = claims[0]
    assert claim.review_status == "unreviewed"
    assert claim.revision == 1
    assert claim.extraction_version == intelligence.LLM_EXTRACTION_VERSION
    assert claim.confidence == intelligence.LLM_CLAIM_CONFIDENCE
    assert claim.category == "qoe_candidate"
    assert claim.field_name == "add_back"
    assert claim.value_text == "$3 million"
    assert claim.value_number == 3.0
    assert claim.created_by_actor_id == lead.actor_id
    # Locator fidelity: the claim binds the VERIFIED chunk's real locator and raw span.
    assert claim.chunk_id == chunks[1].id
    assert claim.source_locator == {"type": "text", "paragraph": 2}
    span = claim.source_span
    assert chunks[1].text[span["start"]:span["end"]] == span["text"] == _QOE_QUOTE
    # LLM claims REPLACE the pattern output: the qoe sentence was NOT also pattern-minted.
    assert {item.extraction_version for item in _claim_rows(db, deal.id)} == {
        intelligence.LLM_EXTRACTION_VERSION
    }
    # Run-level provenance lands in the audit outbox (the route's response is a plain list).
    event = _extraction_events(db)[0]
    assert event.deal_id == deal.id
    assert event.detail["engine"] == "llm"
    assert event.detail["extraction_version"] == intelligence.LLM_EXTRACTION_VERSION
    assert event.detail["llm"]["applied"] is True
    assert event.detail["llm"]["proposed"] == 1
    assert event.detail["llm"]["verified"] == 1
    assert event.detail["llm"]["rejected"] == []
    assert event.detail["llm"]["manifest"]["prompt_id"] == "claim_extraction"

    # The four-eyes review loop is unchanged: the extracting actor cannot approve its own
    # LLM-minted claim; a distinct human reviewer can.
    with pytest.raises(intelligence.IntelligenceConflict, match="distinct reviewer"):
        intelligence.review_claim(
            db, claim.id, ClaimReviewRequest(action="approve", expected_revision=1), lead
        )
    approved, _ = intelligence.review_claim(
        db, claim.id, ClaimReviewRequest(action="approve", expected_revision=1), partner
    )
    assert approved.review_status == "approved"
    assert approved.revision == 2


def test_fabricated_quote_or_chunk_index_is_rejected_and_nothing_is_minted(db, live_mode):
    lead, _, deal, _ = _llm_deal(db)
    provider = _FakeProvider(
        _payload(
            [
                _proposal(quote="Management agreed to a $9 million EBITDA add-back."),
                _proposal(chunk_index=99),
            ]
        )
    )
    claims = _extract(db, deal, lead, provider)
    assert claims == []
    assert _claim_rows(db, deal.id) == []
    event = _extraction_events(db)[0]
    assert event.detail["engine"] == "llm"
    assert event.detail["llm"]["proposed"] == 2
    assert event.detail["llm"]["verified"] == 0
    assert [item["reason"] for item in event.detail["llm"]["rejected"]] == [
        "quote_not_verbatim",
        "invalid_chunk_index",
    ]


def test_claimed_value_absent_from_the_quote_is_rejected(db, live_mode):
    lead, _, deal, _ = _llm_deal(db)
    provider = _FakeProvider(
        _payload(
            [
                # Verbatim quote, but the claimed value_text is not inside it.
                _proposal(value_text="$5 million", value_number=None),
                # value_text checks out, but the claimed number does not appear in the quote.
                _proposal(value_number=5),
                # No scale inference: "$3 million" never verifies a proposed 3000000.
                _proposal(value_number=3_000_000),
            ]
        )
    )
    claims = _extract(db, deal, lead, provider)
    assert claims == []
    assert _claim_rows(db, deal.id) == []
    event = _extraction_events(db)[0]
    assert [item["reason"] for item in event.detail["llm"]["rejected"]] == [
        "value_text_not_in_quote",
        "value_number_not_in_quote",
        "value_number_not_in_quote",
    ]


def test_bare_leading_decimal_never_verifies_the_integer_part():
    """M1 regression: ".3 pts" in a source is 0.3 — it must never verify a claimed 3 (a 10x
    error). Plain integers, decimals, and grouped thousands keep verifying as before."""
    assert intelligence.number_in_quote(3.0, "margin improved .3 pts in the quarter") is False
    assert intelligence.number_in_quote(3.0, "(.3)") is False
    assert intelligence.number_in_quote(3.0, "a 3 percent increase") is True
    assert intelligence.number_in_quote(0.3, "margin improved 0.3 pts") is True
    # The pre-existing digit-boundary discipline is unchanged.
    assert intelligence.number_in_quote(3.0, "the yield was 38.25 percent") is False
    assert intelligence.number_in_quote(25.0, "the yield was 38.25 percent") is False
    assert intelligence.number_in_quote(38.25, "the yield was 38.25 percent") is True
    assert intelligence.number_in_quote(1200.0, "totaling $1,200 thousand") is True


def test_value_number_must_be_the_number_stated_in_value_text(db, live_mode):
    """M2 regression: a quote holding two numbers cannot mint value_text="$200 million" bound
    to value_number=5 — the claimed number must be the one value_text itself states."""
    lead, _, deal, _ = _llm_deal(db)
    contract = intelligence.ingest_text_document(
        db,
        deal.id,
        DocumentTextCreate(
            filename="contract.txt",
            text="The customer committed to $200 million over 5 years.",
        ),
        lead,
    )
    quote = "The customer committed to $200 million over 5 years."
    mismatched = _proposal(
        value_text="$200 million", value_number=5, quote=quote, chunk_index=0, unit=None
    )
    control = {**mismatched, "value_number": 200}
    provider = _FakeProvider(_payload([mismatched, control]))
    claims = _extract(db, deal, lead, provider, document_ids=[contract.id])
    assert [claim.value_number for claim in claims] == [200.0]
    event = _extraction_events(db)[0]
    assert [item["reason"] for item in event.detail["llm"]["rejected"]] == [
        "value_number_not_in_value_text"
    ]


def test_paraphrase_fails_while_whitespace_only_differences_verify(db, live_mode):
    lead, _, deal, document = _llm_deal(db)
    chunks = intelligence.list_chunks(db, document.id, lead)
    whitespace_variant = "Management  proposed a one-time\n$3 million   EBITDA add-back."
    provider = _FakeProvider(
        _payload(
            [
                _proposal(quote=whitespace_variant),
                # One changed word is a paraphrase, not a quote.
                _proposal(quote=_QOE_QUOTE.replace("one-time", "one-off")),
                # Case folding is NOT whitespace normalization; a re-cased quote fails.
                _proposal(quote=_QOE_QUOTE.replace("Management", "management")),
            ]
        )
    )
    claims = _extract(db, deal, lead, provider)
    assert len(claims) == 1
    # The minted span is the chunk's own text, not the model's whitespace rendering.
    assert claims[0].source_span["text"] == _QOE_QUOTE
    assert claims[0].chunk_id == chunks[1].id
    event = _extraction_events(db)[0]
    assert [item["reason"] for item in event.detail["llm"]["rejected"]] == [
        "quote_not_verbatim",
        "quote_not_verbatim",
    ]


def test_mock_mode_stays_pattern_based_and_never_calls_a_provider(db):
    lead, _, deal, _ = _llm_deal(db)
    provider = _FakeProvider(_payload([_proposal(value_text="$9 million")]))
    claims = _extract(db, deal, lead, provider)
    assert provider.calls == 0
    # The pattern extractor's output is identical to the pre-LLM era.
    assert len(claims) == 1
    claim = claims[0]
    assert claim.extraction_version == intelligence.EXTRACTION_VERSION
    assert claim.category == "qoe_candidate"
    assert claim.field_name == "add_back"
    assert claim.value_text == _QOE_TEXT
    assert claim.value_number == 3.0
    assert claim.unit == "USD_millions"
    assert claim.currency == "USD"
    assert claim.confidence == 0.84
    assert claim.review_status == "unreviewed"
    # The LLM path WAS consulted (consent exists), so its mock outcome is auditable.
    event = _extraction_events(db)[0]
    assert event.detail["engine"] == "pattern"
    assert event.detail["llm"] == {"applied": False, "reason": "mock", "manifest": None}


@pytest.mark.parametrize(
    ("external_llm_allowed", "data_classification"),
    [(False, "confidential"), (True, "restricted")],
)
def test_without_consent_live_mode_never_touches_the_llm(
    db, live_mode, external_llm_allowed, data_classification
):
    lead, _, deal, _ = _llm_deal(
        db,
        external_llm_allowed=external_llm_allowed,
        data_classification=data_classification,
    )
    provider = _FakeProvider(_payload([_proposal()]))
    claims = _extract(db, deal, lead, provider)
    assert provider.calls == 0
    assert len(claims) == 1
    assert claims[0].extraction_version == intelligence.EXTRACTION_VERSION
    # The LLM path was never eligible, so extraction stays byte-identical: no audit event.
    assert _extraction_events(db) == []


def test_provider_hard_failure_falls_back_to_the_pattern_extractor(db, live_mode):
    lead, _, deal, _ = _llm_deal(db)
    provider = _FakeProvider("", raises=True)
    claims = _extract(db, deal, lead, provider)
    assert provider.calls == 1
    assert len(claims) == 1
    assert claims[0].extraction_version == intelligence.EXTRACTION_VERSION
    assert claims[0].value_text == _QOE_TEXT
    event = _extraction_events(db)[0]
    assert event.detail["engine"] == "pattern"
    assert event.detail["llm"]["applied"] is False
    assert event.detail["llm"]["reason"] == "error"
