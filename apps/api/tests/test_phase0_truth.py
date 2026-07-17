"""Phase-0 public-data truth regressions.

All tests are offline except the final SEC alignment check, which is skipped when EDGAR is
unavailable. The fixtures intentionally reproduce the former failure modes rather than merely
checking response shapes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src import models  # noqa: F401 - register all tables for the isolated ingestion DB
from src.db.base import Base
from src.models import DocumentChunk, Filing, Workspace
from src.services import (
    edgar_client,
    forensics_service,
    fred_service,
    sec_feeds_service,
    sec_financials,
    sec_ingestion_service,
    usaspending_service,
    valuation_service,
)


def _facts(concepts: dict) -> dict:
    return {"facts": {"us-gaap": concepts}}


def _usd(points: list[dict]) -> dict:
    return {"units": {"USD": points}}


def test_non_december_fye_instants_align_to_duration_end_not_calendar_q4():
    facts = _facts(
        {
            "Revenues": _usd(
                [
                    {
                        "start": "2022-09-25",
                        "end": "2023-09-30",
                        "val": 100.0,
                        "frame": "CY2023",
                        "form": "10-K",
                        "fp": "FY",
                        "filed": "2023-11-01",
                    },
                    {
                        "start": "2023-10-01",
                        "end": "2024-09-28",
                        "val": 120.0,
                        "frame": "CY2024",
                        "form": "10-K",
                        "fp": "FY",
                        "filed": "2024-11-01",
                    },
                ]
            ),
            "CashAndCashEquivalentsAtCarryingValue": _usd(
                [
                    {
                        "end": "2024-09-28",
                        "val": 25.0,
                        "frame": "CY2024Q3I",
                        "form": "10-K",
                        "fp": "FY",
                        "filed": "2024-11-01",
                    },
                    {
                        "end": "2024-12-28",
                        "val": 999.0,
                        "frame": "CY2024Q4I",
                        "form": "10-Q",
                        "fp": "Q1",
                        "filed": "2025-01-30",
                    },
                ]
            ),
            "Assets": _usd(
                [
                    {
                        "end": "2024-09-28",
                        "val": 200.0,
                        "frame": "CY2024Q3I",
                        "form": "10-K",
                        "fp": "FY",
                        "filed": "2024-11-01",
                    }
                ]
            ),
        }
    )

    financials = sec_financials.extract_financials(facts)
    assert financials["fiscal_year_end"] == "2024-09-28"
    assert financials["cash"] == 25.0
    assert financials["sources"]["cash"]["end"] == "2024-09-28"
    forensic = sec_financials.extract_forensic_inputs(facts)
    assert forensic["by_year"]["2024"]["cash"] == 25.0
    assert "2025" not in forensic["years"]


def test_frame_year_labels_annual_periods_for_january_year_end():
    """A January-FYE issuer's FY ending 2026-01-31 carries the CY2025 frame; the frame is the
    period key. ``fy`` cannot be one — it is the reporting filing's fiscal year (see the
    comparative-collapse regression below)."""
    facts = _facts(
        {
            "Revenues": _usd(
                [
                    {
                        "start": "2024-02-01",
                        "end": "2025-01-31",
                        "val": 100.0,
                        "frame": "CY2024",
                        "fy": 2025,
                    },
                    {
                        "start": "2025-02-01",
                        "end": "2026-01-31",
                        "val": 120.0,
                        "frame": "CY2025",
                        "fy": 2026,
                    },
                ]
            ),
            "CashAndCashEquivalentsAtCarryingValue": _usd(
                [
                    {
                        "end": "2025-01-31",
                        "val": 20.0,
                        "frame": "CY2025Q1I",
                        "fy": 2025,
                    },
                    {
                        "end": "2026-01-31",
                        "val": 25.0,
                        "frame": "CY2026Q1I",
                        "fy": 2026,
                    },
                ]
            ),
        }
    )
    financials = sec_financials.extract_financials(facts)
    assert financials["fiscal_year_end"] == "2026-01-31"
    assert financials["sources"]["cash"]["end"] == "2026-01-31"
    forensic = sec_financials.extract_forensic_inputs(facts)
    assert forensic["years"] == ["2024", "2025"]
    assert forensic["by_year"]["2025"]["cash"] == 25.0
    assert sec_financials.extract_trends(facts)["years"] == ["2024", "2025"]


def test_comparative_periods_sharing_the_filing_fy_do_not_collapse():
    """Live-SEC regression: every point in Company Facts carries the *reporting filing's* ``fy``,
    so the three comparative years restated in one 10-K all share it. Keying annual periods by
    ``fy`` collapsed them into a single year — dropping FY2023/FY2024 for real filers (Apple,
    Coca-Cola) and computing "growth" across a three-year gap. Periods must be keyed by frame."""
    one_filing = {"form": "10-K", "fy": 2025, "accn": "acc-2025", "filed": "2025-11-01"}
    facts = _facts(
        {
            "Revenues": _usd(
                [
                    {
                        "start": "2021-01-01",
                        "end": "2021-12-31",
                        "val": 365.0,
                        "frame": "CY2021",
                        "form": "10-K",
                        "fy": 2023,
                        "accn": "acc-2023",
                        "filed": "2023-11-01",
                    },
                    {
                        "start": "2022-01-01",
                        "end": "2022-12-31",
                        "val": 394.0,
                        "frame": "CY2022",
                        "form": "10-K",
                        "fy": 2023,
                        "accn": "acc-2023",
                        "filed": "2023-11-01",
                    },
                    # The current 10-K restates its two comparatives — all three share fy=2025.
                    {
                        "start": "2023-01-01",
                        "end": "2023-12-31",
                        "val": 383.0,
                        "frame": "CY2023",
                        **one_filing,
                    },
                    {
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "val": 391.0,
                        "frame": "CY2024",
                        **one_filing,
                    },
                    {
                        "start": "2025-01-01",
                        "end": "2025-12-31",
                        "val": 416.0,
                        "frame": "CY2025",
                        **one_filing,
                    },
                ]
            ),
        }
    )
    points = edgar_client.annual_points(facts, "Revenues")
    assert [p["frame"] for p in points] == [
        "CY2021",
        "CY2022",
        "CY2023",
        "CY2024",
        "CY2025",
    ]

    trends = sec_financials.extract_trends(facts)
    assert trends["years"] == ["2021", "2022", "2023", "2024", "2025"]
    assert [row["revenue"] for row in trends["rows"]] == [365.0, 394.0, 383.0, 391.0, 416.0]

    financials = sec_financials.extract_financials(facts)
    assert financials["revenue"] == 416.0
    # YoY growth is CY2025 vs CY2024 — not vs a collapsed three-year-old comparative.
    assert financials["revenue_prior"] == 391.0
    assert financials["revenue_growth"] == round((416.0 - 391.0) / 391.0, 4)


def test_instant_fact_does_not_fall_back_to_wrong_balance_sheet_date():
    facts = _facts(
        {
            "Revenues": _usd(
                [
                    {
                        "start": "2023-10-01",
                        "end": "2024-09-28",
                        "val": 120.0,
                        "frame": "CY2024",
                    }
                ]
            ),
            "CashAndCashEquivalentsAtCarryingValue": _usd(
                [{"end": "2024-12-31", "val": 50.0, "frame": "CY2024Q4I"}]
            ),
        }
    )
    assert sec_financials.extract_financials(facts)["cash"] is None


def test_forensic_debt_components_reject_overlapping_aggregate_fallbacks():
    annual_instant = {
        "end": "2024-09-28",
        "frame": "CY2024Q3I",
        "form": "10-K",
        "fp": "FY",
        "filed": "2024-11-01",
    }
    facts = _facts(
        {
            "Revenues": _usd(
                [
                    {
                        "start": "2023-10-01",
                        "end": "2024-09-28",
                        "val": 500.0,
                        "frame": "CY2024",
                    }
                ]
            ),
            # These are valid headline aggregates but overlap the individually modeled tranches.
            "LongTermDebt": _usd([{**annual_instant, "val": 300.0}]),
            "DebtCurrent": _usd([{**annual_instant, "val": 40.0}]),
            "LongTermDebtCurrent": _usd([{**annual_instant, "val": 25.0}]),
            "CashAndCashEquivalentsAtCarryingValue": _usd(
                [{**annual_instant, "val": 100.0}]
            ),
        }
    )

    financials = sec_financials.extract_financials(facts)
    assert financials["total_debt"] == 300.0
    assert financials["sources"]["total_debt"]["concept"] == "LongTermDebt"
    forensic = sec_financials.extract_forensic_inputs(facts)["by_year"]["2024"]
    assert forensic["ltd"] is None
    assert forensic["short_debt"] is None
    assert forensic["ltd_current"] == 25.0


def test_ingestion_refresh_is_accession_and_chunk_idempotent(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    metas = [
        edgar_client.FilingMeta(
            form="10-K",
            filing_date="2025-02-15",
            accession="0000000001-25-000001",
            primary_document="annual.htm",
            primary_doc_url="https://sec.test/annual.htm",
            report_date="2024-12-31",
        ),
        edgar_client.FilingMeta(
            form="10-Q",
            filing_date="2025-05-10",
            accession="0000000001-25-000002",
            primary_document="quarter.htm",
            primary_doc_url="https://sec.test/quarter.htm",
            report_date="2025-03-31",
        ),
    ]
    fetch_calls: list[str] = []
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client,
        "resolve_ticker",
        lambda ticker: {"cik": "0000000001", "ticker": "TEST", "name": "Test Corp"},
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client,
        "get_submissions",
        lambda cik: {"sicDescription": "Test software"},
    )
    monkeypatch.setattr(sec_ingestion_service.edgar_client, "get_company_facts", lambda cik: {})
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client,
        "recent_filings",
        lambda cik, forms, limit: [meta for meta in metas if meta.form in forms][:limit],
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client,
        "fetch_document_text",
        lambda url: fetch_calls.append(url) or "filing text",
    )
    monkeypatch.setattr(
        sec_ingestion_service.sec_financials,
        "extract_financials",
        lambda facts: {"revenue": 100.0, "fiscal_year_end": "2024-12-31"},
    )
    monkeypatch.setattr(
        sec_ingestion_service.sec_financials,
        "extract_trends",
        lambda facts: {"years": [], "rows": [], "revenue_cagr": None},
    )
    monkeypatch.setattr(
        sec_ingestion_service.sec_financials,
        "extract_forensic_inputs",
        lambda facts: {"years": [], "by_year": {}},
    )
    monkeypatch.setattr(
        sec_ingestion_service,
        "extract_sections",
        lambda text: {"Business (Item 1)": "Business section"},
    )
    monkeypatch.setattr(
        sec_ingestion_service,
        "split_paragraphs",
        lambda body: ["First paragraph", "Second paragraph"],
    )

    with Session(engine) as session:
        workspace = Workspace(
            name="Refresh test",
            deal_type="public_equity",
            investment_question="",
            status="draft",
        )
        session.add(workspace)
        session.commit()

        sec_ingestion_service.ingest_company(session, workspace.id, "TEST")
        session.commit()
        ten_k = session.scalar(
            select(Filing).where(Filing.workspace_id == workspace.id, Filing.form_type == "10-K")
        )
        assert ten_k is not None
        # Reproduce a legacy refresh row created before accession_number was populated.
        ten_k.accession_number = None
        session.add(
            DocumentChunk(
                filing_id=ten_k.id,
                workspace_id=workspace.id,
                section="Business (Item 1)",
                chunk_text="legacy duplicate",
                chunk_index=0,
                source_url=ten_k.document_url,
            )
        )
        session.commit()

        sec_ingestion_service.ingest_company(session, workspace.id, "TEST")
        session.commit()

        assert session.scalar(
            select(func.count()).select_from(Filing).where(Filing.workspace_id == workspace.id)
        ) == 2
        assert set(
            session.scalars(
                select(Filing.accession_number).where(Filing.workspace_id == workspace.id)
            )
        ) == {"0000000001-25-000001", "0000000001-25-000002"}
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.filing_id == ten_k.id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        assert [(chunk.chunk_index, chunk.chunk_text) for chunk in chunks] == [
            (0, "First paragraph"),
            (1, "Second paragraph"),
        ]
        assert ten_k.section_count == 2
        assert fetch_calls == ["https://sec.test/annual.htm"]


def test_fred_yoy_uses_dates_for_daily_and_quarterly_frequencies():
    daily_points = [
        {"date": "2024-01-03", "value": 100.0},
        *[
            {"date": f"2024-12-{day:02d}", "value": 108.0 + day / 100}
            for day in range(10, 31)
        ],
        {"date": "2025-01-03", "value": 110.0},
    ]
    daily = fred_service._summarize_series("DGS10", daily_points)
    assert daily is not None
    assert daily["yoy_change"] == 0.1

    quarterly = fred_service._summarize_series(
        "GDPC1",
        [
            {"date": "2023-10-01", "value": 90.0},
            {"date": "2024-10-01", "value": 100.0},
            {"date": "2025-01-01", "value": 102.0},
            {"date": "2025-04-01", "value": 104.0},
            {"date": "2025-07-01", "value": 106.0},
            {"date": "2025-10-01", "value": 108.0},
        ],
    )
    assert quarterly is not None
    assert quarterly["yoy_change"] == 0.08


def test_fred_yoy_is_none_without_a_frequency_appropriate_prior():
    result = fred_service._summarize_series(
        "DGS10",
        [
            {"date": "2023-01-01", "value": 100.0},
            {"date": "2025-01-03", "value": 110.0},
        ],
    )
    assert result is not None
    assert result["yoy_change"] is None

    # A neighboring quarter is not the requested prior-year quarter.
    quarterly = fred_service._summarize_series(
        "GDPC1",
        [
            {"date": "2024-07-01", "value": 100.0},
            {"date": "2025-10-01", "value": 110.0},
        ],
    )
    assert quarterly is not None
    assert quarterly["yoy_change"] is None


def _award(
    award_id: str,
    amount: float | None,
    pop_end: str,
    agency: str = "Agency A",
) -> dict:
    return {
        "Award ID": award_id,
        "Recipient Name": "Test Contractor",
        "Awarding Agency": agency,
        "Awarding Sub Agency": f"{agency} sub",
        "Award Amount": amount,
        "Description": f"Award {award_id}",
        "Period of Performance Current End Date": pop_end,
        "Period of Performance Start Date": "2024-01-01",
    }


def test_usaspending_paginates_agencies_and_awards_for_true_totals(monkeypatch):
    today = datetime.now(timezone.utc).date()
    in_window = (today + timedelta(days=100)).isoformat()
    calls: list[tuple[str, int | None]] = []
    award_a = _award("A", 60.0, in_window, "Agency A")
    award_b = _award("B", 40.0, in_window, "Agency B")

    def fake_post(path: str, payload: dict) -> dict:
        calls.append((path, payload.get("page")))
        page = payload.get("page", 1)
        if path.endswith("awarding_agency/"):
            if page == 1:
                return {
                    "results": [{"name": "Agency A", "amount": 60.0}],
                    "page_metadata": {"hasNext": True, "next": 2},
                }
            return {
                "results": [{"name": "Agency B", "amount": 40.0}],
                "page_metadata": {"hasNext": False, "next": None},
            }
        if path.endswith("spending_by_award_count/"):
            return {"results": {"contracts": 2}}
        if path.endswith("spending_by_award/"):
            if page == 1:
                return {
                    "results": [award_a],
                    "page_metadata": {"hasNext": True, "next": 2},
                }
            return {
                "results": [award_a, award_b],
                "page_metadata": {"hasNext": False, "next": None},
            }
        raise AssertionError(path)

    monkeypatch.setattr(usaspending_service, "_post", fake_post)
    profile = usaspending_service.award_profile("Test Contractor, Inc.")

    assert profile["total_obligations"] == 100.0
    assert profile["top_agency"] == "Agency A"
    assert profile["top_agency_pct"] == 0.6
    assert profile["award_count"] == 2
    assert [award["award_id"] for award in profile["top_awards"]] == ["A", "B"]
    assert profile["recompete_within_24mo"]["count"] == 2
    assert profile["recompete_within_24mo"]["value"] == 100.0
    assert ("/search/spending_by_category/awarding_agency/", 2) in calls
    assert ("/search/spending_by_award/", 2) in calls


def test_usaspending_rejects_non_advancing_or_incomplete_pages(monkeypatch):
    repeated = {"results": [{"name": "Agency", "amount": 1}],
                "page_metadata": {"hasNext": True}}
    monkeypatch.setattr(usaspending_service, "_post", lambda path, payload: repeated)
    with pytest.raises(usaspending_service.UsaSpendingError, match="did not advance"):
        usaspending_service._all_pages("/test", {"limit": 1})

    monkeypatch.setattr(
        usaspending_service,
        "_post",
        lambda path, payload: {
            "results": [{"page": payload["page"]}],
            "page_metadata": {"hasNext": True, "next": payload["page"]},
        },
    )
    with pytest.raises(usaspending_service.UsaSpendingError, match="did not advance"):
        usaspending_service._all_pages("/test", {"limit": 1})

    def missing_amount(path: str, payload: dict) -> dict:
        if path.endswith("awarding_agency/"):
            return {"results": [{"name": "Agency", "amount": None}],
                    "page_metadata": {"hasNext": False}}
        if path.endswith("award_count/"):
            return {"results": {"contracts": 0}}
        return {"results": [], "page_metadata": {"hasNext": False}}

    monkeypatch.setattr(usaspending_service, "_post", missing_amount)
    with pytest.raises(usaspending_service.UsaSpendingError, match="numeric obligation"):
        usaspending_service.award_profile("Test")

    def invalid_count(path: str, payload: dict) -> dict:
        if path.endswith("awarding_agency/"):
            return {"results": [], "page_metadata": {"hasNext": False}}
        return {"results": {"contracts": "not-a-number"}}

    monkeypatch.setattr(usaspending_service, "_post", invalid_count)
    with pytest.raises(usaspending_service.UsaSpendingError, match="non-numeric"):
        usaspending_service.award_profile("Test")


def _form4_transaction(code: str, acquired_disposed: str, shares: int = 10) -> str:
    return f"""
    <nonDerivativeTransaction>
      <transactionDate><value>2026-01-10</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{acquired_disposed}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    """


def test_form4_classification_uses_transaction_code_not_acquired_disposed_flag():
    xml = (
        "<ownershipDocument>"
        "<reportingOwner><reportingOwnerId><rptOwnerName>Example Insider</rptOwnerName>"
        "</reportingOwnerId></reportingOwner>"
        + _form4_transaction("P", "A")
        + _form4_transaction("S", "D")
        + _form4_transaction("A", "A")
        + _form4_transaction("F", "D")
        + "</ownershipDocument>"
    ).encode()
    rows = sec_feeds_service._parse_form4(xml, "https://sec.test/form4")

    assert [row["transaction_code"] for row in rows] == ["P", "S", "A", "F"]
    assert [row["type"] for row in rows] == ["buy", "sell", "other", "other"]
    assert rows[2]["acquired_disposed_code"] == "A"
    assert rows[3]["acquired_disposed_code"] == "D"


def _metric(metrics: list[dict], key: str) -> dict:
    return next(metric for metric in metrics if metric["key"] == key)


def test_forensics_uses_average_balances_and_requires_complete_net_debt():
    prior = {"receivables": 60.0, "inventory": 40.0, "payables": 20.0}
    current = {
        "revenue": 800.0,
        "cogs": 400.0,
        "receivables": 100.0,
        "inventory": 80.0,
        "payables": 60.0,
        "ltd": 250.0,
        "ltd_current": 50.0,
        "short_debt": 0.0,
        "cash": 120.0,
    }
    metrics = forensics_service._qoe(current, prior)
    assert _metric(metrics, "dso")["value"] == 36.5
    assert _metric(metrics, "dio")["value"] == 54.8
    assert _metric(metrics, "dpo")["value"] == 36.5
    assert _metric(metrics, "net_debt")["value"] == 180.0

    partial_debt = dict(current, short_debt=None)
    partial_metrics = forensics_service._qoe(partial_debt, prior)
    assert _metric(partial_metrics, "net_debt")["value"] is None
    assert _metric(partial_metrics, "leverage_nd_ebitda")["value"] is None
    no_prior_metrics = forensics_service._qoe(current, None)
    assert _metric(no_prior_metrics, "dso")["value"] is None


def test_piotroski_missing_signal_is_unscored_not_a_failure():
    current = {
        "assets": 100.0,
        "net_income": 10.0,
        "cfo": 12.0,
        "ltd": 20.0,
        "current_assets": 50.0,
        "current_liabilities": 25.0,
        "revenue": 100.0,
        "gross_profit": 50.0,
        "shares_out": None,
    }
    prior = {
        "assets": 90.0,
        "net_income": 8.0,
        "cfo": 9.0,
        "ltd": 25.0,
        "current_assets": 40.0,
        "current_liabilities": 25.0,
        "revenue": 90.0,
        "gross_profit": 40.0,
        "shares_out": None,
    }
    score = forensics_service._piotroski(current, prior)
    assert score["available"] is False
    assert score["value"] is None
    assert score["rating"] == "n/a"
    assert "No share dilution" in score["note"]
    assert next(c for c in score["components"] if c["name"] == "No share dilution")["value"] is None


def test_reduced_beneish_value_is_display_only_and_not_threshold_scored():
    prior = {
        "receivables": 8.0,
        "revenue": 90.0,
        "gross_profit": 36.0,
        "current_assets": 45.0,
        "ppe_net": 28.0,
        "assets": 90.0,
        "sga": 9.0,
        "current_liabilities": 18.0,
        "ltd": 32.0,
        "ltd_current": 4.0,
        "net_income": 4.0,
        "cfo": 5.0,
        "da": None,
    }
    current = {
        "receivables": 10.0,
        "revenue": 100.0,
        "gross_profit": 40.0,
        "current_assets": 50.0,
        "ppe_net": 30.0,
        "assets": 100.0,
        "sga": 10.0,
        "current_liabilities": 20.0,
        "ltd": 30.0,
        "ltd_current": 5.0,
        "net_income": 5.0,
        "cfo": 6.0,
        "da": None,
    }
    score = forensics_service._beneish(current, prior)
    assert score["available"] is True
    assert score["value"] is not None
    assert score["rating"] == "unscored"
    assert "not comparable" in score["interpretation"]


def test_beneish_lvgi_does_not_double_count_current_debt_or_zero_fill_ltd():
    complete = {
        "receivables": 8.0,
        "revenue": 90.0,
        "gross_profit": 36.0,
        "current_assets": 45.0,
        "ppe_net": 28.0,
        "assets": 90.0,
        "sga": 9.0,
        "current_liabilities": 18.0,
        "ltd": 32.0,
        "ltd_current": 999.0,
        "net_income": 4.0,
        "cfo": 5.0,
        "da": 4.0,
    }
    assert forensics_service._lvg_debt(complete) == 50.0
    missing_ltd = dict(complete, ltd=None)
    assert forensics_service._lvg_debt(missing_ltd) is None
    score = forensics_service._beneish(dict(complete, revenue=100.0), missing_ltd)
    assert score["available"] is False
    lvgi = next(item for item in score["components"] if item["name"] == "LVGI leverage")
    assert lvgi["value"] is None


class _ValuationTarget:
    name = "Valuation Test"
    total_debt = 250.0
    cash = 100.0

    def __init__(self, latest: dict, debt_concept: str | None):
        self.financials = {
            "forensic_inputs": {"years": ["2025"], "by_year": {"2025": latest}},
            "sources": {
                "total_debt": (
                    {"concept": debt_concept, "value": self.total_debt}
                    if debt_concept
                    else None
                )
            },
        }


def test_valuation_core_uses_fcff_and_never_zero_fills_net_debt():
    latest = {
        "operating_income": 80.0,
        "da": 20.0,
        "ltd": 200.0,
        "ltd_current": 50.0,
        "short_debt": None,
        "cash": 100.0,
        "equity": 500.0,
        "cfo": 100.0,
        "interest": 20.0,
        "capex": 30.0,
    }
    strict = valuation_service._core_inputs(_ValuationTarget(latest, None))
    assert strict["net_debt"] is None
    assert strict["fcf_base"] == 85.8  # CFO + interest*(1-21%) - capex

    reported = valuation_service._core_inputs(
        _ValuationTarget(latest, "DebtLongtermAndShorttermCombinedAmount")
    )
    assert reported["net_debt"] == 150.0
    assert "combined" in reported["net_debt_basis"]

    long_term_only = valuation_service._core_inputs(_ValuationTarget(latest, "LongTermDebt"))
    assert long_term_only["net_debt"] == 150.0
    assert "short-term borrowing is not included" in long_term_only["net_debt_basis"]

    no_cash = valuation_service._core_inputs(
        _ValuationTarget(dict(latest, cash=None), "DebtLongtermAndShorttermCombinedAmount")
    )
    assert no_cash["net_debt"] is None
    assert valuation_service._core_inputs(
        _ValuationTarget(dict(latest, interest=None), "DebtLongtermAndShorttermCombinedAmount")
    )["fcf_base"] is None


def test_legacy_dcf_assumptions_now_describe_fcff_without_false_unlevered_label():
    dcf = valuation_service.compute_dcf(100.0, 0.10)
    assumptions = " ".join(dcf["assumptions"])
    assert "FCFF" in assumptions
    assert "after-tax interest" in assumptions
    assert "FCF base = CFO - capex" not in assumptions
    assert "unlevered" not in assumptions.lower()
    assert valuation_service.compute_dcf(-10.0, 0.10)["enterprise_value"] is None


@pytest.mark.parametrize(
    ("cik", "fiscal_month"),
    [("0000320193", "09"), ("0000789019", "06"), ("0001535527", "01")],
)
def test_live_named_issuer_fiscal_alignment(sec_online, cik, fiscal_month):
    if not sec_online:
        pytest.skip("SEC EDGAR unreachable")
    try:
        facts = edgar_client.get_company_facts(cik)
    except edgar_client.EdgarError as exc:
        pytest.skip(f"SEC EDGAR company facts unavailable: {exc}")
    financials = sec_financials.extract_financials(facts)
    fiscal_end = financials["fiscal_year_end"]
    assert fiscal_end and fiscal_end[5:7] == fiscal_month
    assert financials["sources"]["cash"] is not None
    assert financials["sources"]["cash"]["end"] == fiscal_end
    forensic = sec_financials.extract_forensic_inputs(facts)
    fiscal_label = sec_financials._period_year(
        {
            "fy": financials["sources"]["revenue"]["fy"],
            "frame": financials["sources"]["revenue"]["frame"],
            "end": fiscal_end,
        }
    )
    assert forensic["by_year"][fiscal_label]["cash"] == financials["cash"]
