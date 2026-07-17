"""G08 — unified cross-corpus Q&A over public filings + confidential data-room docs.

Offline: a workspace with a public 10-K chunk and a deal-linked data room with a confidential
chunk. One question is answered with citations drawn from BOTH corpora, each labeled public vs
confidential; confidential content is never mislabeled public; and a question neither corpus
supports abstains rather than fabricating.
"""
from __future__ import annotations

import hashlib

import pytest

from src.db.session import SessionLocal
from src.models import (
    DataRoomChunk,
    DataRoomDocument,
    Deal,
    DocumentChunk,
    Filing,
    Fund,
    Organization,
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


def _seed_public_filing(session, ws_id):
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


def _seed_confidential_dataroom(session, ws_id):
    """Create a deal linked to the workspace with one confidential data-room chunk."""
    org = Organization(name="Cross Corpus Org", slug=f"xc-{ws_id[:8]}")
    session.add(org)
    session.flush()
    fund = Fund(organization_id=org.id, name="Fund I")
    session.add(fund)
    session.flush()
    deal = Deal(
        organization_id=org.id,
        fund_id=fund.id,
        workspace_id=ws_id,
        code=f"XC-{ws_id[:6]}",
        name="Cross Corpus Deal",
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
        "/api/workspaces", json={"name": "Cross corpus", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        _seed_public_filing(session, ws_id)
        deal_id = _seed_confidential_dataroom(session, ws_id)
        session.commit()
    return ws_id, deal_id


def test_answer_cites_both_corpora_with_confidentiality_labels(cross_corpus_workspace):
    ws_id, deal_id = cross_corpus_workspace
    with SessionLocal() as session:
        result = cross_corpus_qa_service.answer(
            session,
            ws_id,
            "What is the customer concentration and the adjusted EBITDA add-back?",
        )

    assert result["status"] == "answered"
    assert result["deal_id"] == deal_id
    assert result["corpora"]["confidential_dataroom"]["available"] is True

    corpora = {c["corpus"] for c in result["citations"]}
    assert corpora == {"public_filing", "confidential_dataroom"}

    for citation in result["citations"]:
        if citation["corpus"] == "public_filing":
            assert citation["confidential"] is False
            assert citation["label"] == "Public SEC filing"
            assert "concentration" in citation["quote"].lower()
        else:
            assert citation["confidential"] is True
            assert citation["label"] == "Confidential data room"
            assert "add-back" in citation["quote"]
            assert citation["provenance"]["filename"] == "QoE Notes.txt"

    # Confidential content must NEVER surface under a public label.
    for citation in result["citations"]:
        if "$3 million" in citation["quote"] or "add-back" in citation["quote"]:
            assert citation["corpus"] == "confidential_dataroom"
            assert citation["confidential"] is True


def test_filings_only_workspace_answers_labeled_public(client):
    ws_id = client.post(
        "/api/workspaces", json={"name": "Public only", "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        _seed_public_filing(session, ws_id)
        session.commit()
        result = cross_corpus_qa_service.answer(
            session, ws_id, "How concentrated is revenue in the largest customer?"
        )

    assert result["status"] in {"answered", "partial"}
    assert result["deal_id"] is None
    assert result["corpora"]["confidential_dataroom"]["available"] is False
    assert result["citations"]
    assert all(c["corpus"] == "public_filing" for c in result["citations"])
    assert all(c["confidential"] is False for c in result["citations"])


_SHARED_SENTENCE = (
    "The retention rate of subscription customers exceeded 95 percent during the fiscal year."
)


def test_score_ties_prefer_the_public_corpus(client):
    """Regression: the tie-break comment promised public-over-confidential, but the preference
    tuple gave confidential the higher component under max()/descending order — an identically
    scored sentence surfaced as a confidential quote when a public disclosure said the same."""
    ws_id = client.post(
        "/api/workspaces", json={"name": "Tie break", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        filing = Filing(
            workspace_id=ws_id,
            company_name="Tie Corp",
            ticker="TIE",
            cik="0000000002",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000002-25-000001",
            document_url="https://www.sec.gov/Archives/tie-10k.htm",
        )
        session.add(filing)
        session.flush()
        session.add(
            DocumentChunk(
                filing_id=filing.id,
                workspace_id=ws_id,
                section="Item 7 MD&A",
                chunk_index=0,
                chunk_text=_SHARED_SENTENCE,
                source_url=filing.document_url,
            )
        )
        org = Organization(name="Tie Org", slug=f"tie-{ws_id[:8]}")
        session.add(org)
        session.flush()
        fund = Fund(organization_id=org.id, name="Fund I")
        session.add(fund)
        session.flush()
        deal = Deal(
            organization_id=org.id,
            fund_id=fund.id,
            workspace_id=ws_id,
            code=f"TIE-{ws_id[:6]}",
            name="Tie Deal",
            target_company="Tie Corp",
        )
        session.add(deal)
        session.flush()
        content = _SHARED_SENTENCE.encode("utf-8")
        document = DataRoomDocument(
            deal_id=deal.id,
            logical_document_id=f"tie-{ws_id[:8]}",
            version=1,
            title="Retention analysis",
            filename="Retention.txt",
            original_filename="Retention.txt",
            extension=".txt",
            content_type="text/plain",
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            raw_bytes=content,
            document_metadata={},
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
                text=_SHARED_SENTENCE,
                normalized_text=_SHARED_SENTENCE.casefold(),
                content_hash=hashlib.sha256(content).hexdigest(),
                char_count=len(_SHARED_SENTENCE),
            )
        )
        session.commit()
        result = cross_corpus_qa_service.answer(
            session, ws_id, "What was the retention rate of subscription customers?"
        )

    assert result["citations"]
    assert [c["corpus"] for c in result["citations"]] == ["public_filing"]


def test_abstains_when_neither_corpus_matches(cross_corpus_workspace):
    ws_id, _ = cross_corpus_workspace
    with SessionLocal() as session:
        result = cross_corpus_qa_service.answer(
            session, ws_id, "What is the company's lithium mining strategy in Antarctica?"
        )
    assert result["status"] == "abstained"
    assert result["citations"] == []
    assert "No answer was fabricated" in result["answer"]


def test_cross_corpus_endpoint_contract(client, cross_corpus_workspace):
    ws_id, _ = cross_corpus_workspace
    resp = client.post(
        f"/api/workspaces/{ws_id}/cross-corpus-qa",
        json={"question": "What is the customer concentration and the adjusted EBITDA add-back?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "answered"
    assert {c["corpus"] for c in body["citations"]} == {
        "public_filing",
        "confidential_dataroom",
    }
