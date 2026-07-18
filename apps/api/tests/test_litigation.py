"""G67 — litigation & proceedings: Item 3 extraction, 8-K legal events, honest degradation.

Offline throughout: 10-K text and the 8-K feed are fixture/monkeypatched. The discipline under
test: a workspace whose 10-K text is unavailable reports ``unavailable`` — the absence of an
extracted section is NEVER presented as an absence of litigation.
"""
from __future__ import annotations

import pytest

from src.db.session import SessionLocal
from src.services import edgar_client, litigation_service, sec_feeds_service
from src.services.edgar_client import EdgarError
from src.services.filing_sections import extract_legal_proceedings

_DOC_URL = "https://www.sec.gov/Archives/edgar/data/1234/000123/lit-10k.htm"

# A 10-K-shaped fixture: TOC entries first (short spans), then real item bodies. The Item 3 body
# is substantive (> ITEM3_REVIEW_MIN_CHARS) so the review flag fires.
TENK_TEXT = (
    "TABLE OF CONTENTS Item 1. Business 4 Item 1A. Risk Factors 20 "
    "Item 3. Legal Proceedings 45 Item 4. Mine Safety Disclosures 46 Item 7. MD&A 50 "
    "Item 1. Business We make widgets. " + ("business prose. " * 30)
    + "Item 1A. Risk Factors Customer concentration. " + ("risk prose. " * 30)
    + "Item 3. Legal Proceedings We are a defendant in a putative class action in the Northern "
    "District of Demo alleging misrepresentation of widget durability; we intend to defend "
    "vigorously and cannot estimate a range of loss at this time. " + ("litigation prose. " * 40)
    + "Item 4. Mine Safety Disclosures Not applicable. "
    "Item 7. Management's Discussion and Analysis Revenue increased. " + ("mdna prose. " * 30)
    + "Item 8. Financial Statements"
)

TENK_NO_ITEM3 = (
    "TABLE OF CONTENTS Item 1. Business 4 Item 7. MD&A 50 "
    "Item 1. Business We make widgets. " + ("business prose. " * 30)
    + "Item 7. Management's Discussion and Analysis Revenue increased. " + ("mdna prose. " * 30)
    + "Item 8. Financial Statements"
)


def _fake_events(events_rows, status="available", error=None):
    def fake(session, workspace_id):
        return {
            "workspace_id": workspace_id,
            "events": events_rows,
            "source_status": status,
            "source_error": error,
            "generated_at": None,
        }

    return fake


_BANKRUPTCY_EVENT = {
    "date": "2025-03-01",
    "form": "8-K",
    "items": [{"code": "1.03", "label": "Bankruptcy or Receivership"}],
    "accession": "0001-25-000001",
    "url": "https://www.sec.gov/Archives/edgar/data/1234/000125/bk-8k.htm",
    "significant": False,
}
_OTHER_EVENT = {
    "date": "2025-02-01",
    "form": "8-K",
    "items": [{"code": "8.01", "label": "Other Events"}],
    "accession": "0001-25-000002",
    "url": "https://www.sec.gov/Archives/edgar/data/1234/000126/oe-8k.htm",
    "significant": False,
}


def _make_workspace(*, with_tenk: bool = True, cik: str | None = "0000001234") -> str:
    from src.models import Filing, Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="Litigation Co", deal_type="buyout")
        )
        s.add(
            Target(
                workspace_id=ws.id,
                name="Litigation Co",
                target_type="public_company",
                cik=cik,
            )
        )
        if with_tenk:
            s.add(
                Filing(
                    workspace_id=ws.id,
                    company_name="Litigation Co",
                    form_type="10-K",
                    filing_date="2025-02-15",
                    accession_number="0001-25-000010",
                    document_url=_DOC_URL,
                )
            )
        s.commit()
        return ws.id


def _build(workspace_id: str) -> dict:
    with SessionLocal() as s:
        return litigation_service.build(s, workspace_id)


# --- Item 3 section extraction ---------------------------------------------------------------


def test_extract_legal_proceedings_present_and_bounded():
    section = extract_legal_proceedings(TENK_TEXT)
    assert "putative class action" in section
    assert "litigation prose" in section  # the real body won over the short TOC entry
    # Bounded by the Item 4 header: nothing from Item 4 onward leaks in.
    assert "Mine Safety" not in section
    assert "Financial Statements" not in section


def test_extract_legal_proceedings_absent_returns_empty():
    assert extract_legal_proceedings(TENK_NO_ITEM3) == ""
    assert extract_legal_proceedings("") == ""


def test_extract_legal_proceedings_short_body_still_located():
    text = (
        "TABLE OF CONTENTS Item 3. Legal Proceedings 45 Item 4. Mine Safety 46 "
        "Item 1. Business We make widgets and sell them worldwide through partners. "
        "Item 3. Legal Proceedings None that are material to our operations at this time. "
        "Item 4. Mine Safety Disclosures Not applicable."
    )
    section = extract_legal_proceedings(text)
    assert "None that are material" in section
    assert "Not applicable" not in section


# --- build(): the litigation payload ---------------------------------------------------------


def test_build_available_with_item3_and_legal_events(monkeypatch):
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: TENK_TEXT)
    monkeypatch.setattr(
        sec_feeds_service, "events", _fake_events([_BANKRUPTCY_EVENT, _OTHER_EVENT])
    )
    wid = _make_workspace()
    out = _build(wid)
    assert out["status"] == "available"
    assert out["item3"]["present"] is True
    assert out["item3"]["filing"]["document_url"] == _DOC_URL
    chunks = out["item3"]["excerpt_chunks"]
    assert chunks, "Item 3 must be chunked"
    for chunk in chunks:
        assert chunk["section"] == "Legal Proceedings (Item 3)"
        assert chunk["source_url"] == _DOC_URL  # every chunk cites the filing
    assert any("putative class action" in chunk["text"] for chunk in chunks)
    # Only the explicitly legal item code (1.03) enters the timeline; 8.01 does not.
    assert len(out["events"]) == 1
    assert out["events"][0]["items"][0]["code"] == "1.03"
    # The taxonomy limitation is stated, not papered over.
    assert "8.01" in out["note"]


def test_build_terse_item3_body_survives_chunking(monkeypatch):
    text = (
        "TABLE OF CONTENTS Item 3. Legal Proceedings 45 Item 4. Mine Safety 46 "
        "Item 1. Business We make widgets and sell them worldwide through partners. "
        "Item 3. Legal Proceedings None material at this time. "
        "Item 4. Mine Safety Disclosures Not applicable."
    )
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: text)
    monkeypatch.setattr(sec_feeds_service, "events", _fake_events([]))
    wid = _make_workspace()
    out = _build(wid)
    assert out["item3"]["present"] is True
    assert len(out["item3"]["excerpt_chunks"]) == 1  # short body kept whole, never dropped


def test_build_unavailable_when_tenk_text_unfetchable(monkeypatch):
    def boom(url):
        raise EdgarError("EDGAR down")

    monkeypatch.setattr(edgar_client, "fetch_document_text", boom)
    monkeypatch.setattr(sec_feeds_service, "events", _fake_events([]))
    wid = _make_workspace()
    out = _build(wid)
    assert out["status"] == "unavailable"  # never clean
    assert out["item3"]["present"] is False
    assert "NOT evidence" in out["item3"]["note"]


def test_build_unavailable_when_no_tenk_on_file(monkeypatch):
    monkeypatch.setattr(sec_feeds_service, "events", _fake_events([]))
    wid = _make_workspace(with_tenk=False)
    out = _build(wid)
    assert out["status"] == "unavailable"
    assert out["item3"]["present"] is False
    assert "NOT evidence" in out["item3"]["note"]


def test_build_partial_when_item3_not_located(monkeypatch):
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: TENK_NO_ITEM3)
    monkeypatch.setattr(sec_feeds_service, "events", _fake_events([]))
    wid = _make_workspace()
    out = _build(wid)
    assert out["status"] == "partial"
    assert out["item3"]["present"] is False
    assert "NOT evidence" in out["item3"]["note"]


def test_build_partial_when_events_feed_degraded(monkeypatch):
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: TENK_TEXT)
    monkeypatch.setattr(
        sec_feeds_service,
        "events",
        _fake_events([], status="unavailable", error="SEC EDGAR submissions are temporarily unavailable."),
    )
    wid = _make_workspace()
    out = _build(wid)
    assert out["status"] == "partial"  # Item 3 present but the timeline may be incomplete
    assert out["item3"]["present"] is True
    assert "degraded" in out["note"]


# --- risk_flags: same contract as forensics/sec_feeds ----------------------------------------


def test_risk_flags_shape_and_emission(monkeypatch):
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: TENK_TEXT)
    monkeypatch.setattr(
        sec_feeds_service, "events", _fake_events([_BANKRUPTCY_EVENT, _OTHER_EVENT])
    )
    wid = _make_workspace()
    with SessionLocal() as s:
        flags = litigation_service.risk_flags(s, wid)
    titles = {f["title"] for f in flags}
    assert "Bankruptcy or receivership 8-K filed" in titles
    assert "Legal Proceedings disclosure in the 10-K (Item 3)" in titles
    for f in flags:
        assert f["risk_category"] == "legal_regulatory"
        assert f["severity"] in ("low", "medium", "high", "critical")
        assert 0 <= f["severity_score"] <= 9
        assert f["workstream_owner"] == "legal_regulatory"
        ev = f["evidence"]
        assert ev["claim"] and ev["claim_type"] in ("fact", "calculation", "inference", "assumption")
        assert ev["agent_name"] == "litigation"
        assert ev["source_type"] == "sec_filing"
    item3_flag = next(f for f in flags if "Item 3" in f["title"])
    assert "not a judgment of merit" in item3_flag["finding"]  # honest framing
    assert item3_flag["evidence"]["source_url"] == _DOC_URL


def test_risk_flags_degrade_to_empty_never_fabricated(monkeypatch):
    def boom(url):
        raise EdgarError("EDGAR down")

    monkeypatch.setattr(edgar_client, "fetch_document_text", boom)
    monkeypatch.setattr(
        sec_feeds_service, "events", _fake_events([], status="unavailable", error="down")
    )
    wid = _make_workspace()
    with SessionLocal() as s:
        assert litigation_service.risk_flags(s, wid) == []
    # No CIK -> no flags either (and no crash) — the analysis pipeline is never broken.
    wid2 = _make_workspace(cik=None)
    with SessionLocal() as s:
        assert litigation_service.risk_flags(s, wid2) == []


# --- endpoint contract -----------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _wire_router(client):
    """Mount the Wave 6 router (integrator wires it into main.py; no-op once that lands)."""
    from src.main import app
    from src.routers import research_wave6

    have = {getattr(r, "path", "") for r in app.routes}
    if "/api/workspaces/{workspace_id}/litigation" not in have:
        app.include_router(research_wave6.router)
    yield


def test_litigation_endpoint_contract(client, monkeypatch):
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: TENK_TEXT)
    monkeypatch.setattr(sec_feeds_service, "events", _fake_events([_BANKRUPTCY_EVENT]))
    wid = _make_workspace()
    body = client.get(f"/api/workspaces/{wid}/litigation").json()
    assert body["workspace_id"] == wid
    assert body["status"] == "available"
    assert body["item3"]["present"] is True
    assert body["events"][0]["items"][0]["code"] == "1.03"
    assert body["note"]


def test_litigation_endpoint_unknown_workspace_404(client):
    assert client.get("/api/workspaces/nope/litigation").status_code == 404


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
