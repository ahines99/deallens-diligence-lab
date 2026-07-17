"""G54 — grounded synthesis for cross-corpus Q&A: the G04 fail-closed fluency pass extended to
the public-filings + confidential-data-room answer path.

Every path is offline and deterministic: a fake provider stands in for the live LLM exactly as in
``test_grounded_synthesis``. The tests pin the confidentiality contract — [PUBLIC]/[CONFIDENTIAL]
labels reach the prompt, the citations list is byte-for-byte unchanged in every path, and a
restricted or non-consenting workspace NEVER constructs a provider, even with ``grounded=True``.
"""
from __future__ import annotations

import copy
import hashlib

import pytest

from src.agents import llm_provider
from src.config import settings
from src.db.session import SessionLocal
from src.models import (
    DataRoomChunk,
    DataRoomDocument,
    Deal,
    DocumentChunk,
    Filing,
    Fund,
    Organization,
    Workspace,
)
from src.services import cross_corpus_qa_service

_PUBLIC_RISK = (
    "Customer concentration remains a material risk. Our largest customer represented "
    "approximately 14 percent of consolidated revenue during the fiscal year."
)
_CONFIDENTIAL_QOE = (
    "Management identified a one-time add-back of $3 million to adjusted EBITDA for the fiscal "
    "year, presented in the confidential quality-of-earnings analysis."
)
_QUESTION = "What is the customer concentration and the adjusted EBITDA add-back?"


class _FakeProvider:
    """Stands in for LiveProvider.complete, returning a canned rewrite and capturing the prompt."""

    model = "claude-test"

    def __init__(self, text: str) -> None:
        self._text = text
        self.seen_user_prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.seen_user_prompts.append(user)
        return self._text


def _seed_public_filing(session, ws_id: str) -> None:
    filing = Filing(
        workspace_id=ws_id,
        company_name="Fixture Corp",
        ticker="FIX",
        cik="0000000001",
        form_type="10-K",
        filing_date="2025-02-01",
        accession_number="0000000001-25-000001",
        document_url="https://www.sec.gov/Archives/fixture-10k.htm",
    )
    session.add(filing)
    session.flush()
    session.add(
        DocumentChunk(
            filing_id=filing.id,
            workspace_id=ws_id,
            section="Item 1A Risk Factors",
            chunk_index=0,
            chunk_text=_PUBLIC_RISK,
            source_url=filing.document_url,
        )
    )


def _seed_confidential_dataroom(session, ws_id: str) -> str:
    """Create a deal linked to the workspace with one confidential data-room chunk."""
    org = Organization(name="Synthesis Org", slug=f"gs-{ws_id[:8]}")
    session.add(org)
    session.flush()
    fund = Fund(organization_id=org.id, name="Fund I")
    session.add(fund)
    session.flush()
    deal = Deal(
        organization_id=org.id,
        fund_id=fund.id,
        workspace_id=ws_id,
        code=f"GS-{ws_id[:6]}",
        name="Synthesis Deal",
        target_company="Fixture Corp",
    )
    session.add(deal)
    session.flush()
    content = _CONFIDENTIAL_QOE.encode("utf-8")
    document = DataRoomDocument(
        deal_id=deal.id,
        logical_document_id=f"qoe-{ws_id[:8]}",
        version=1,
        title="Quality of Earnings",
        filename="QoE Notes.txt",
        original_filename="QoE Notes.txt",
        extension=".txt",
        content_type="text/plain",
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        raw_bytes=content,
        document_metadata={"workstream": "financial", "confidential": True},
    )
    session.add(document)
    session.flush()
    session.add(
        DataRoomChunk(
            deal_id=deal.id,
            document_id=document.id,
            ordinal=1,
            locator_type="text",
            locator={"type": "text", "paragraph": 1},
            text=_CONFIDENTIAL_QOE,
            normalized_text=_CONFIDENTIAL_QOE.casefold(),
            content_hash=hashlib.sha256(_CONFIDENTIAL_QOE.encode("utf-8")).hexdigest(),
            char_count=len(_CONFIDENTIAL_QOE),
        )
    )
    return deal.id


@pytest.fixture()
def cross_corpus_workspace(client):
    ws_id = client.post(
        "/api/workspaces", json={"name": "Grounded cross corpus", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        _seed_public_filing(session, ws_id)
        _seed_confidential_dataroom(session, ws_id)
        session.commit()
    return ws_id


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    yield


def _extractive(ws_id: str) -> dict:
    with SessionLocal() as session:
        return cross_corpus_qa_service.answer(session, ws_id, _QUESTION)


def _counting_factory(record: list, text: str = "anything"):
    def factory():
        record.append(True)
        return _FakeProvider(text)

    return factory


# --------------------------------------------------------------- faithful rewrite applied


def test_faithful_rewrite_applied_with_labels_and_citations_intact(
    cross_corpus_workspace, live_mode
):
    result = _extractive(cross_corpus_workspace)
    assert result["status"] == "answered"
    citations_before = copy.deepcopy(result["citations"])

    # Reuse the extracted sentences verbatim (reordered): no number added or dropped → faithful.
    faithful = "In brief: " + " ".join(reversed([c["quote"] for c in result["citations"]]))
    provider = _FakeProvider(faithful)
    out = cross_corpus_qa_service.maybe_synthesize_cross_corpus(
        result, external_allowed=True, provider_factory=lambda: provider
    )

    assert out["answer"] == faithful
    assert out["grounded"]["applied"] is True
    assert out["grounded"]["reason"] == "applied"
    assert out["method"].endswith("+grounded_llm")
    man = out["grounded"]["manifest"]
    assert man["prompt_id"] == "cross_corpus_synthesis"
    assert len(man["prompt_hash"]) == 64
    assert man["model"] == "claude-test"

    # Citations are byte-for-byte unchanged: same labels, flags, quotes, provenance.
    assert out["citations"] == citations_before

    # Every quote reached the LLM with its confidentiality label prefixed.
    (user_prompt,) = provider.seen_user_prompts
    for citation in citations_before:
        label = "[CONFIDENTIAL]" if citation["confidential"] else "[PUBLIC]"
        assert f"{label} {citation['quote']}" in user_prompt
    assert any(c["confidential"] for c in citations_before)
    assert any(not c["confidential"] for c in citations_before)


# --------------------------------------------------------------- drift is rejected


def test_fabricated_number_is_rejected_and_extractive_answer_served(
    cross_corpus_workspace, live_mode
):
    result = _extractive(cross_corpus_workspace)
    citations_before = copy.deepcopy(result["citations"])
    fabricated = result["answer"] + " Roughly 42 percent of revenue is recurring."
    out = cross_corpus_qa_service.maybe_synthesize_cross_corpus(
        result, external_allowed=True, provider_factory=lambda: _FakeProvider(fabricated)
    )
    assert out["answer"] == result["answer"]  # extractive preserved byte-for-byte
    assert out["method"] == cross_corpus_qa_service.METHOD  # no +grounded_llm suffix
    assert out["grounded"]["applied"] is False
    assert out["grounded"]["reason"] == "audit_rejected"
    assert out["grounded"]["manifest"]["prompt_id"] == "cross_corpus_synthesis"
    assert out["citations"] == citations_before


# --------------------------------------------------------------- abstention / gating


def test_abstention_is_untouched_and_provider_never_called(cross_corpus_workspace, live_mode):
    with SessionLocal() as session:
        result = cross_corpus_qa_service.answer(
            session,
            cross_corpus_workspace,
            "What is the company's lithium mining strategy in Antarctica?",
        )
    assert result["status"] == "abstained"
    calls: list = []
    out = cross_corpus_qa_service.maybe_synthesize_cross_corpus(
        result, external_allowed=True, provider_factory=_counting_factory(calls)
    )
    assert out["answer"] == cross_corpus_qa_service.ABSTENTION
    assert out["citations"] == []
    assert out["grounded"] == {"applied": False, "reason": "not_eligible"}
    assert calls == []  # never asked the LLM to invent evidence


def test_mock_mode_never_calls_the_provider(cross_corpus_workspace):
    # Default test env is mock: even with consent, the provider factory is never invoked.
    result = _extractive(cross_corpus_workspace)
    calls: list = []
    out = cross_corpus_qa_service.maybe_synthesize_cross_corpus(
        result, external_allowed=True, provider_factory=_counting_factory(calls)
    )
    assert out["answer"] == result["answer"]
    assert out["grounded"] == {"applied": False, "reason": "mock"}
    assert calls == []


def test_no_consent_never_calls_the_provider_even_live(cross_corpus_workspace, live_mode):
    # The confidentiality rule: without consent the quotes must not leave the box, live or not.
    result = _extractive(cross_corpus_workspace)
    calls: list = []
    out = cross_corpus_qa_service.maybe_synthesize_cross_corpus(
        result, external_allowed=False, provider_factory=_counting_factory(calls)
    )
    assert out["answer"] == result["answer"]
    assert out["grounded"] == {"applied": False, "reason": "no_consent"}
    assert calls == []


# --------------------------------------------------------------- router contract


def _set_governance(ws_id: str, *, allowed: bool, classification: str) -> None:
    with SessionLocal() as session:
        ws = session.get(Workspace, ws_id)
        ws.external_llm_allowed = allowed
        ws.data_classification = classification
        session.commit()


def _guard_live_provider(monkeypatch) -> list:
    """Fail loudly if the route ever constructs the real provider; return the call recorder."""
    constructed: list = []

    def _init(self) -> None:
        constructed.append(True)
        raise AssertionError("LiveProvider must never be constructed in this test")

    monkeypatch.setattr(llm_provider.LiveProvider, "__init__", _init)
    return constructed


def test_route_grounded_on_consenting_workspace_in_mock_mode(
    client, cross_corpus_workspace, monkeypatch
):
    _set_governance(cross_corpus_workspace, allowed=True, classification="confidential")
    constructed = _guard_live_provider(monkeypatch)
    resp = client.post(
        f"/api/workspaces/{cross_corpus_workspace}/cross-corpus-qa",
        json={"question": _QUESTION, "grounded": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grounded"]["applied"] is False
    assert body["grounded"]["reason"] == "mock"
    assert body["grounded"]["manifest"] is None
    # The extractive contract is untouched: labeled citations from both corpora.
    assert {c["corpus"] for c in body["citations"]} == {"public_filing", "confidential_dataroom"}
    assert constructed == []


def test_route_restricted_classification_never_calls_the_llm(
    client, cross_corpus_workspace, monkeypatch
):
    # Consent flag alone is not enough: a restricted classification keeps confidential quotes in.
    _set_governance(cross_corpus_workspace, allowed=True, classification="restricted")
    constructed = _guard_live_provider(monkeypatch)
    resp = client.post(
        f"/api/workspaces/{cross_corpus_workspace}/cross-corpus-qa",
        json={"question": _QUESTION, "grounded": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grounded"]["applied"] is False
    assert body["grounded"]["reason"] == "no_consent"
    assert constructed == []


def test_route_without_grounded_flag_is_pure_extractive(client, cross_corpus_workspace):
    resp = client.post(
        f"/api/workspaces/{cross_corpus_workspace}/cross-corpus-qa",
        json={"question": _QUESTION},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grounded"] is None  # existing consumers see the unchanged contract
    assert body["method"] == cross_corpus_qa_service.METHOD
