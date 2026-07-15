"""Cited filings Q&A: extractive answers, abstention, and the memo faithfulness report."""
from __future__ import annotations

import pytest

from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing, Workspace
from src.services import retrieval_service


@pytest.fixture()
def filing_workspace(client):
    """A workspace with two deterministic 10-K style chunks, no network."""
    ws_id = client.post(
        "/api/workspaces", json={"name": "QA fixture", "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        assert session.get(Workspace, ws_id) is not None
        filing = Filing(
            workspace_id=ws_id,
            company_name="Fixture Corp",
            ticker="FIX",
            cik="0000000001",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000001-25-000001",
            document_url="https://www.sec.gov/Archives/fixture-10k.htm",
            is_synthetic=False,
        )
        session.add(filing)
        session.flush()
        session.add_all(
            [
                DocumentChunk(
                    filing_id=filing.id,
                    workspace_id=ws_id,
                    section="Item 1A Risk Factors",
                    chunk_index=0,
                    chunk_text=(
                        "Customer concentration remains a material risk. Our largest customer "
                        "represented approximately 14 percent of consolidated revenue during "
                        "the fiscal year, and the loss of this customer would materially harm "
                        "our operating results."
                    ),
                ),
                DocumentChunk(
                    filing_id=filing.id,
                    workspace_id=ws_id,
                    section="Item 7 MD&A",
                    chunk_index=1,
                    chunk_text=(
                        "Revenue increased 12 percent year over year, driven primarily by "
                        "subscription growth. Operating expenses grew more slowly than revenue, "
                        "expanding operating margin by two percentage points."
                    ),
                ),
            ]
        )
        session.commit()
    return ws_id


def test_bm25_ranks_the_topically_relevant_chunk_first(filing_workspace):
    with SessionLocal() as session:
        ranked = retrieval_service.retrieve(
            session, filing_workspace, "customer concentration risk largest customer", k=2
        )
        assert ranked, "expected a BM25 match"
        assert ranked[0].chunk.section == "Item 1A Risk Factors"
        assert ranked[0].score > 0


def test_qa_answers_verbatim_with_filing_citations(client, filing_workspace):
    resp = client.post(
        f"/api/workspaces/{filing_workspace}/qa",
        json={"question": "How concentrated is revenue in the largest customer?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "answered"
    assert "14 percent" in body["answer"]
    citation = body["citations"][0]
    assert citation["section"] == "Item 1A Risk Factors"
    assert citation["document_url"].startswith("https://www.sec.gov/")
    # Extractive guarantee: the quoted sentence appears verbatim in the answer.
    assert citation["quote"] in body["answer"]


def test_qa_abstains_rather_than_fabricating(client, filing_workspace):
    resp = client.post(
        f"/api/workspaces/{filing_workspace}/qa",
        json={"question": "What is the company's lithium mining strategy in Antarctica?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "abstained"
    assert body["citations"] == []
    assert "No answer was fabricated" in body["answer"]


def test_qa_rejects_an_empty_question(client, filing_workspace):
    resp = client.post(f"/api/workspaces/{filing_workspace}/qa", json={"question": "   "})
    assert resp.status_code == 422


def test_memo_faithfulness_report_flags_unresolved_refs(client, filing_workspace):
    from src.models import Memo

    with SessionLocal() as session:
        session.add(
            Memo(
                workspace_id=filing_workspace,
                memo_type="ic_memo",
                title="Fixture memo",
                markdown_content=(
                    "## Financial profile\n"
                    "Revenue grew 12% year over year [EV-001].\n"
                    "EBITDA margin reached 21% with no citation attached.\n"
                    "A dangling reference cites [EV-999]."
                ),
            )
        )
        session.commit()

    resp = client.get(f"/api/workspaces/{filing_workspace}/memo/faithfulness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    memo_report = next(d for d in body["documents"] if d["document_type"] == "ic_memo")
    # EV-001 and EV-999 are cited; neither exists in this fixture workspace.
    assert set(memo_report["unresolved_refs"]) == {"EV-001", "EV-999"}
    assert memo_report["fully_resolved"] is False
    assert memo_report["citation_count"] == 2
    # The uncited numeric sentence is surfaced for human review.
    assert any(
        "21%" in sentence for sentence in memo_report["uncited_numeric_sentences"]
    )


def test_qa_rejects_an_overlong_question(client, filing_workspace):
    """LOW: an unbounded question is a CPU/bandwidth amplification vector — cap it."""
    resp = client.post(
        f"/api/workspaces/{filing_workspace}/qa",
        json={"question": "revenue " * 500},  # ~4000 chars, over the 2000 cap
    )
    assert resp.status_code == 422


def test_thin_single_term_match_is_labeled_partial(client, filing_workspace):
    """LOW: a one-term lexical hit must not present as a confident full answer."""
    # 'customer' matches the risk-factor sentence, but the other four terms don't appear —
    # coverage falls below the threshold, so the answer is 'partial', not 'answered'.
    resp = client.post(
        f"/api/workspaces/{filing_workspace}/qa",
        json={"question": "customer aardvark zeppelin quarterly lithium"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "partial"
    assert body["retrieval"]["coverage"] < 0.5
    assert body["citations"]  # citations still resolve
