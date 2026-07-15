"""G07 — cross-year 10-K risk-factor drift: added / removed / materially changed, with citations.

Offline: two synthetic 10-Ks with a known risk-item set, asserting the classifier separates a
newly disclosed risk, a dropped risk, and a same-risk material rewrite while omitting an unchanged
risk. A workspace holding only one 10-K yields an explicit ``unavailable`` rather than a fabricated
diff.
"""
from __future__ import annotations

import pytest

from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing
from src.services import filing_diff_service

_SUPPLIER = (
    "We depend on a limited number of key suppliers, and any disruption in their operations "
    "could materially and adversely affect our ability to manufacture products."
)
_CUSTOMER_OLD = (
    "Our largest customer represented approximately 14 percent of consolidated revenue during "
    "the fiscal year, and the loss of this customer would materially harm our operating results."
)
_CUSTOMER_NEW = (
    "Our largest customer represented approximately 22 percent of consolidated revenue during "
    "the fiscal year, and the loss of this customer would materially harm our operating results "
    "and cash flows."
)
_CURRENCY = (
    "Fluctuations in foreign currency exchange rates could adversely affect our reported results "
    "because a significant portion of our sales are denominated in euros."
)
_CYBER = (
    "Cybersecurity threats and attacks on our information systems could result in the "
    "unauthorized disclosure of sensitive data, regulatory penalties, and reputational damage."
)


def _add_10k(session, ws_id, *, filing_date, accession, url, items):
    filing = Filing(
        workspace_id=ws_id,
        company_name="Fixture Corp",
        ticker="FIX",
        cik="0000000001",
        form_type="10-K",
        filing_date=filing_date,
        accession_number=accession,
        document_url=url,
    )
    session.add(filing)
    session.flush()
    session.add_all(
        [
            DocumentChunk(
                filing_id=filing.id,
                workspace_id=ws_id,
                section="Item 1A Risk Factors",
                chunk_index=index,
                chunk_text=text,
                source_url=url,
            )
            for index, text in enumerate(items)
        ]
    )
    return filing.id


@pytest.fixture()
def two_year_workspace(client):
    ws_id = client.post(
        "/api/workspaces", json={"name": "Risk diff", "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        _add_10k(
            session,
            ws_id,
            filing_date="2024-02-01",
            accession="0000000001-24-000001",
            url="https://www.sec.gov/Archives/fixture-10k-2024.htm",
            items=[_SUPPLIER, _CUSTOMER_OLD, _CURRENCY],
        )
        _add_10k(
            session,
            ws_id,
            filing_date="2025-02-01",
            accession="0000000001-25-000001",
            url="https://www.sec.gov/Archives/fixture-10k-2025.htm",
            items=[_SUPPLIER, _CUSTOMER_NEW, _CYBER],
        )
        session.commit()
    return ws_id


def test_risk_diff_classifies_added_removed_and_changed(two_year_workspace):
    with SessionLocal() as session:
        result = filing_diff_service.diff_risk_factors(session, two_year_workspace)

    assert result["source_status"] == "ok"
    assert result["older_filing"]["filing_date"] == "2024-02-01"
    assert result["newer_filing"]["filing_date"] == "2025-02-01"

    # Added: the cybersecurity risk only appears in the newer 10-K, cited into it.
    assert len(result["added"]) == 1
    added = result["added"][0]
    assert "Cybersecurity" in added["quote"]
    assert added["filing_date"] == "2025-02-01"
    assert added["document_url"].startswith("https://www.sec.gov/")

    # Removed: the foreign-currency risk was dropped, cited into the older 10-K.
    assert len(result["removed"]) == 1
    removed = result["removed"][0]
    assert "foreign currency" in removed["quote"]
    assert removed["filing_date"] == "2024-02-01"

    # Changed: the same customer-concentration risk, reworded, cited into BOTH filings.
    assert len(result["changed"]) == 1
    changed = result["changed"][0]
    assert "14 percent" in changed["old"]["quote"]
    assert "22 percent" in changed["new"]["quote"]
    assert changed["old"]["filing_date"] == "2024-02-01"
    assert changed["new"]["filing_date"] == "2025-02-01"
    assert 0.5 <= changed["similarity"] < 0.98

    # The unchanged supplier risk is neither added, removed, nor changed.
    quotes = (
        [c["quote"] for c in result["added"]]
        + [c["quote"] for c in result["removed"]]
        + [c["old"]["quote"] for c in result["changed"]]
        + [c["new"]["quote"] for c in result["changed"]]
    )
    assert all("key suppliers" not in quote for quote in quotes)


def test_risk_diff_endpoint_contract(client, two_year_workspace):
    resp = client.get(f"/api/workspaces/{two_year_workspace}/filings/risk-diff")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_status"] == "ok"
    assert len(body["added"]) == 1
    assert len(body["removed"]) == 1
    assert len(body["changed"]) == 1


def test_single_filing_is_unavailable_not_fabricated(client):
    ws_id = client.post(
        "/api/workspaces", json={"name": "Single 10-K", "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        _add_10k(
            session,
            ws_id,
            filing_date="2025-02-01",
            accession="0000000009-25-000001",
            url="https://www.sec.gov/Archives/only-10k.htm",
            items=[_SUPPLIER, _CUSTOMER_NEW],
        )
        session.commit()
        result = filing_diff_service.diff_risk_factors(session, ws_id)

    assert result["source_status"] == "unavailable"
    assert result["added"] == []
    assert result["removed"] == []
    assert result["changed"] == []
    assert "two 10-K" in result["note"]
