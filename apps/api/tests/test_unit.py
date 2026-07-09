"""Offline unit tests: taxonomy, financial math, XBRL mapping, section extraction, evidence refs."""
from __future__ import annotations

import datetime

from src.agents.financial_analyst import FinancialAnalyst
from src.schemas.common import RiskCategory, Workstream
from src.seed import loader
from src.services import evidence_service, fred_service, sec_financials, usaspending_service
from src.services.filing_sections import extract_sections

VALID_CATEGORIES = set(RiskCategory.__args__)
VALID_WORKSTREAMS = set(Workstream.__args__)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_taxonomy_integrity():
    tax = loader.risk_taxonomy()
    cats = tax["categories"]
    assert len(cats) == 10
    assert {c["slug"] for c in cats} == VALID_CATEGORIES
    for c in cats:
        assert c["workstream_owner"] in VALID_WORKSTREAMS
        assert c["signals"]
    assert tax["severity_scale"]["critical"] == [9, 10]


def test_financial_math():
    assert FinancialAnalyst.rule_of_40(0.24, 0.16) == 0.40
    assert FinancialAnalyst.implied_ebitda(100.0, 0.2) == 20.0
    assert FinancialAnalyst.median([3, 1, 2]) == 2
    assert FinancialAnalyst.median([1, 2, 3, 4]) == 2.5
    assert FinancialAnalyst.rule_of_40(None, 0.1) is None


def test_xbrl_financials_mapping():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    {"end": "2023-12-31", "val": 100.0, "form": "10-K", "fp": "FY", "frame": "CY2023"},
                    {"end": "2024-12-31", "val": 120.0, "form": "10-K", "fp": "FY", "frame": "CY2024"},
                ]}},
                "GrossProfit": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 72.0, "form": "10-K", "fp": "FY", "frame": "CY2024"},
                ]}},
                "OperatingIncomeLoss": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 12.0, "form": "10-K", "fp": "FY", "frame": "CY2024"},
                ]}},
                "NetIncomeLoss": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 6.0, "form": "10-K", "fp": "FY", "frame": "CY2024"},
                ]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 50.0, "form": "10-K", "fp": "FY", "frame": "CY2024Q4I"},
                ]}},
            }
        }
    }
    fin = sec_financials.extract_financials(facts)
    assert fin["revenue"] == 120.0
    assert round(fin["revenue_growth"], 4) == 0.2
    assert round(fin["gross_margin"], 4) == 0.6
    assert round(fin["operating_margin"], 4) == 0.1
    assert round(fin["net_margin"], 4) == 0.05
    assert round(fin["rule_of_40"], 4) == 0.3
    assert fin["cash"] == 50.0
    assert fin["sources"]["revenue"]["concept"] == "Revenues"


def test_section_extraction():
    text = (
        "TABLE OF CONTENTS Item 1. Business 4 Item 1A. Risk Factors 20 Item 7. MD&A 50 "
        "Item 1. Business We are a software company that provides compliance tools to manufacturers. "
        "Our platform screens suppliers. " + ("filler business prose. " * 40) +
        "Item 1A. Risk Factors We depend on a small number of large customers for a significant portion "
        "of revenue, and the loss of a major customer could harm results. " + ("risk prose. " * 40) +
        "Item 1B. Unresolved Staff Comments None. "
        "Item 7. Management's Discussion and Analysis Revenue increased due to new customers. "
        + ("mdna prose. " * 40) + "Item 8. Financial Statements"
    )
    secs = extract_sections(text)
    assert "Risk Factors (Item 1A)" in secs
    assert "small number of large customers" in secs["Risk Factors (Item 1A)"]
    assert "Business (Item 1)" in secs


def test_evidence_ref_allocation():
    # Create an empty (no-ticker, offline) workspace, then allocate refs.
    wid = client_create_empty()
    from src.db.session import SessionLocal

    with SessionLocal() as s:
        e1 = evidence_service.create(
            s, wid, claim="c1", claim_type="fact", source_name="src", source_type="xbrl",
            evidence_text="t1", confidence=0.9, agent_name="financial_analyst",
        )
        e2 = evidence_service.create(
            s, wid, claim="c2", claim_type="calculation", source_name="src", source_type="xbrl",
            evidence_text="t2", confidence=0.9, agent_name="financial_analyst",
        )
        s.commit()
        assert e1.ref == "EV-001"
        assert e2.ref == "EV-002"
        assert evidence_service.known_refs(s, wid) == {"EV-001", "EV-002"}


def test_extract_trends():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    {"end": "2022-12-31", "val": 100.0, "frame": "CY2022"},
                    {"end": "2023-12-31", "val": 110.0, "frame": "CY2023"},
                    {"end": "2024-12-31", "val": 121.0, "frame": "CY2024"},
                ]}},
                "GrossProfit": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 72.6, "frame": "CY2024"},
                ]}},
            }
        }
    }
    tr = sec_financials.extract_trends(facts)
    assert tr["years"] == ["2022", "2023", "2024"]
    assert len(tr["rows"]) == 3
    assert tr["rows"][-1]["revenue"] == 121.0
    assert round(tr["rows"][-1]["gross_margin"], 2) == 0.60
    assert round(tr["revenue_cagr"], 4) == 0.1  # 100 -> 121 over 2 years = 10%


def test_usaspending_helpers():
    assert usaspending_service.clean_recipient("Leidos Holdings, Inc.") == "Leidos"
    assert usaspending_service.clean_recipient("BOOZ ALLEN HAMILTON HOLDING CORP") == "Booz Allen Hamilton"
    assert usaspending_service._parse_date("2027-03-15") == datetime.date(2027, 3, 15)
    assert usaspending_service._parse_date(None) is None


def test_fred_sector_series():
    soft = fred_service.sectors_series("Services-Prepackaged Software")
    assert "FEDFUNDS" in soft and "DGS10" in soft
    mfg = fred_service.sectors_series("Semiconductor Manufacturing")
    assert "INDPRO" in mfg


def client_create_empty() -> str:
    from src.db.session import SessionLocal
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(s, WorkspaceCreate(name="Empty", deal_type="buyout"))
        return ws.id
