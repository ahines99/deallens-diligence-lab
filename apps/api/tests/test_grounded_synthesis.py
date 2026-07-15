"""G04 — grounded synthesis: fluent rewrite gated by the citation auditor, failing closed.

Every path is offline and deterministic: a fake provider stands in for the live LLM, exactly as
``test_governed_evidence_integrity`` monkeypatches ``LiveProvider.complete``.
"""
from __future__ import annotations

import pytest

from src.config import settings
from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing, Workspace
from src.services import filings_qa_service, grounded_qa


class _FakeProvider:
    """Stands in for LiveProvider.complete, returning a canned rewrite."""

    model = "claude-test"

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, system: str, user: str) -> str:
        return self._text


def _extractive_result() -> dict:
    return {
        "workspace_id": "ws",
        "question": "How concentrated is revenue?",
        "method": "extractive_bm25",
        "status": "answered",
        "answer": "Our largest customer represented approximately 14 percent of revenue.",
        "citations": [
            {"quote": "Our largest customer represented approximately 14 percent of revenue."}
        ],
        "retrieval": {"coverage": 0.8},
    }


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    yield


def test_faithful_rewrite_is_applied_and_records_manifest(live_mode):
    result = _extractive_result()
    faithful = "Its largest customer was about 14 percent of revenue."
    out = grounded_qa.maybe_synthesize(
        result, external_allowed=True, provider_factory=lambda: _FakeProvider(faithful)
    )
    assert out["answer"] == faithful
    assert out["grounded"]["applied"] is True
    assert out["method"].endswith("+grounded_llm")
    assert out["grounded"]["manifest"]["prompt_id"] == "grounded_synthesis"
    assert len(out["grounded"]["manifest"]["prompt_hash"]) == 64


def test_fabricated_number_is_rejected_and_extractive_answer_is_served(live_mode):
    result = _extractive_result()
    fabricated = "Its largest customer was about 18 percent of revenue."  # 14 -> 18
    out = grounded_qa.maybe_synthesize(
        result, external_allowed=True, provider_factory=lambda: _FakeProvider(fabricated)
    )
    assert out["answer"] == result["answer"]  # extractive preserved byte-for-byte
    assert out["grounded"]["applied"] is False
    assert out["grounded"]["reason"] == "audit_rejected"


def test_fabricated_citation_is_rejected_and_extractive_answer_is_served(live_mode):
    result = _extractive_result()
    fabricated = result["answer"] + " [EV-999]"  # invents a citation not in the source
    out = grounded_qa.maybe_synthesize(
        result, external_allowed=True, provider_factory=lambda: _FakeProvider(fabricated)
    )
    assert out["answer"] == result["answer"]
    assert out["grounded"]["applied"] is False
    assert out["grounded"]["reason"] == "audit_rejected"


def test_empty_rewrite_falls_back_to_extractive(live_mode):
    result = _extractive_result()
    out = grounded_qa.maybe_synthesize(
        result, external_allowed=True, provider_factory=lambda: _FakeProvider("   ")
    )
    assert out["answer"] == result["answer"]
    assert out["grounded"]["applied"] is False


def test_abstention_is_preserved_and_llm_never_called(live_mode):
    called = []

    def factory():
        called.append(True)
        return _FakeProvider("anything")

    abstained = {
        "workspace_id": "ws",
        "question": "unrelated",
        "method": "extractive_bm25",
        "status": "abstained",
        "answer": filings_qa_service.ABSTENTION,
        "citations": [],
        "retrieval": {"coverage": 0.0},
    }
    out = grounded_qa.maybe_synthesize(abstained, external_allowed=True, provider_factory=factory)
    assert out["answer"] == filings_qa_service.ABSTENTION
    assert out["grounded"]["applied"] is False
    assert called == []  # never asked the LLM to invent evidence


def test_no_consent_stays_extractive(live_mode):
    result = _extractive_result()
    out = grounded_qa.maybe_synthesize(
        result, external_allowed=False, provider_factory=lambda: _FakeProvider("x")
    )
    assert out["answer"] == result["answer"]
    assert out["grounded"]["reason"] == "no_consent"


def test_mock_mode_stays_extractive(monkeypatch):
    # Default mock mode: even with consent, no LLM runs and the answer is unchanged.
    result = _extractive_result()
    out = grounded_qa.maybe_synthesize(
        result, external_allowed=True, provider_factory=lambda: _FakeProvider("x")
    )
    assert out["answer"] == result["answer"]
    assert out["grounded"]["reason"] == "mock"


# --- integration through the QA service (consent gating + default extractive) ---------------


def _qa_workspace() -> str:
    ws_id = None
    with SessionLocal() as session:
        ws = Workspace(name="grounded qa", deal_type="public_equity", status="complete")
        session.add(ws)
        session.flush()
        ws_id = ws.id
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
        session.add(
            DocumentChunk(
                filing_id=filing.id,
                workspace_id=ws_id,
                section="Item 1A Risk Factors",
                chunk_index=0,
                chunk_text=(
                    "Customer concentration remains a material risk. Our largest customer "
                    "represented approximately 14 percent of consolidated revenue during the "
                    "fiscal year."
                ),
            )
        )
        session.commit()
    return ws_id


def test_ask_default_is_purely_extractive():
    ws_id = _qa_workspace()
    with SessionLocal() as session:
        result = filings_qa_service.ask(
            session, ws_id, "How concentrated is revenue in the largest customer?"
        )
    assert result["status"] == "answered"
    assert "14 percent" in result["answer"]
    assert "grounded" not in result  # grounded path off by default


def test_ask_grounded_without_consent_stays_extractive():
    ws_id = _qa_workspace()  # workspace has external_llm_allowed=False by default
    with SessionLocal() as session:
        result = filings_qa_service.ask(
            session,
            ws_id,
            "How concentrated is revenue in the largest customer?",
            grounded=True,
        )
    assert "14 percent" in result["answer"]
    assert result["grounded"]["applied"] is False
    assert result["grounded"]["reason"] in {"no_consent", "mock"}
