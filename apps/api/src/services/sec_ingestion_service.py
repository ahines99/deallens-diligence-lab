"""Real SEC ingestion: resolve a ticker, pull XBRL financials, filing metadata, and 10-K sections.

This replaces the earlier synthetic path. Everything here is real EDGAR data with real source URLs.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DocumentChunk, Filing, Target, Workspace
from src.services import edgar_client, sec_financials
from src.services.common import NotFound
from src.services.edgar_client import EdgarError
from src.services.filing_sections import extract_sections, split_paragraphs

logger = logging.getLogger("deallens.ingest")

FILING_FORMS = ("10-K", "10-Q", "8-K")
DEFAULT_FILING_LIMIT = 8


def search(query: str) -> list[dict]:
    """Ticker/name search over the SEC company list."""
    try:
        return edgar_client.search_companies(query)
    except EdgarError:
        return []


def ingest_company(session: Session, workspace_id: str, ticker: str, filing_limit: int = DEFAULT_FILING_LIMIT) -> Target:
    """Resolve a ticker and populate the workspace's Target, filings, and 10-K chunks from EDGAR."""
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")

    info = edgar_client.resolve_ticker(ticker)
    cik = info["cik"]
    name = info["name"]
    submissions = edgar_client.get_submissions(cik)
    sector = submissions.get("sicDescription") or ""

    try:
        facts = edgar_client.get_company_facts(cik)
        fin = sec_financials.extract_financials(facts)
        fin["trends"] = sec_financials.extract_trends(facts)
        fin["forensic_inputs"] = sec_financials.extract_forensic_inputs(facts)
    except EdgarError as exc:
        logger.warning("No XBRL company facts for %s: %s", ticker, exc)
        fin = {}

    # --- Filings (metadata) ---
    metas = edgar_client.recent_filings(cik, FILING_FORMS, filing_limit)
    existing_acc = {
        f.accession for f in session.scalars(select(Filing).where(Filing.workspace_id == workspace_id))
    }
    tenk_filing: Filing | None = None
    for m in metas:
        if m.accession in existing_acc:
            continue
        filing = Filing(
            workspace_id=workspace_id,
            company_name=name,
            ticker=info["ticker"],
            cik=cik,
            form_type=m.form,
            filing_date=m.filing_date,
            accession_number=m.accession,
            document_url=m.primary_doc_url,
            is_synthetic=False,
        )
        session.add(filing)
        existing_acc.add(m.accession)
        if m.form == "10-K" and tenk_filing is None:
            tenk_filing = filing
    session.flush()

    # --- 10-K sections -> chunks (for risk extraction / retrieval) ---
    business_text = ""
    if tenk_filing is None:
        latest = edgar_client.recent_filings(cik, ("10-K",), 1)
        if latest:
            # A 10-K exists but was outside the metadata window; add it.
            m = latest[0]
            tenk_filing = Filing(
                workspace_id=workspace_id,
                company_name=name,
                ticker=info["ticker"],
                cik=cik,
                form_type="10-K",
                filing_date=m.filing_date,
                accession_number=m.accession,
                document_url=m.primary_doc_url,
                is_synthetic=False,
            )
            session.add(tenk_filing)
            session.flush()

    if tenk_filing is not None and tenk_filing.document_url:
        try:
            text = edgar_client.fetch_document_text(tenk_filing.document_url)
            sections = extract_sections(text)
            business_text = sections.get("Business (Item 1)", "")
            index = 0
            for section_name, body in sections.items():
                for para in split_paragraphs(body):
                    session.add(
                        DocumentChunk(
                            filing_id=tenk_filing.id,
                            workspace_id=workspace_id,
                            section=section_name,
                            chunk_text=para,
                            chunk_index=index,
                            source_url=tenk_filing.document_url,
                        )
                    )
                    index += 1
            tenk_filing.section_count = index
            session.flush()
        except EdgarError as exc:
            logger.warning("Failed to fetch/parse 10-K for %s: %s", ticker, exc)

    # --- Target upsert ---
    target = session.scalar(select(Target).where(Target.workspace_id == workspace_id))
    if target is None:
        target = Target(workspace_id=workspace_id)
        session.add(target)
    target.name = name
    target.target_type = "public_company"
    target.ticker = info["ticker"]
    target.cik = cik
    target.sector = sector
    target.description = _company_description(business_text, sector, name)
    target.is_synthetic = False
    target.data_source = "SEC EDGAR (XBRL + 10-K)"
    if fin:
        target.revenue = fin.get("revenue")
        target.revenue_growth = fin.get("revenue_growth")
        target.gross_margin = fin.get("gross_margin")
        target.operating_margin = fin.get("operating_margin")
        target.net_income = fin.get("net_income")
        target.net_margin = fin.get("net_margin")
        target.rnd_pct = fin.get("rnd_pct")
        target.rule_of_40 = fin.get("rule_of_40")
        target.cash = fin.get("cash")
        target.total_debt = fin.get("total_debt")
        target.fiscal_year_end = fin.get("fiscal_year_end")
        target.financials = fin
    session.flush()
    ws.target_id = target.id
    return target


def _company_description(business_text: str, sector: str, name: str) -> str:
    if business_text:
        # Skip table-of-contents noise (number/"Item"-heavy lines) and take real prose sentences.
        sentences = re.split(r"(?<=[.!?])\s+", business_text)
        good: list[str] = []
        for s in sentences:
            s = s.strip()
            if len(s) < 60:
                continue
            digits = sum(c.isdigit() for c in s)
            if digits > len(s) * 0.12:  # TOC / page-number lines
                continue
            if s.count("Item ") > 1:
                continue
            good.append(s)
            if sum(len(x) for x in good) > 480:
                break
        desc = " ".join(good).strip()
        if len(desc) >= 60:
            return (desc[:700].rsplit(" ", 1)[0] + "…") if len(desc) > 700 else desc
    if sector:
        return f"{name} — {sector} (per SEC EDGAR)."
    return name


def list_filings(session: Session, workspace_id: str) -> list[Filing]:
    return list(
        session.scalars(
            select(Filing)
            .where(Filing.workspace_id == workspace_id)
            .order_by(Filing.filing_date.desc())
        )
    )
