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
    existing_filings = list(
        session.scalars(select(Filing).where(Filing.workspace_id == workspace_id))
    )

    def filing_key(
        accession: str | None,
        form: str,
        filing_date: str,
        document_url: str | None,
    ) -> str:
        """Use accession when present; otherwise retain an idempotent metadata fallback."""

        clean_accession = (accession or "").strip()
        if clean_accession:
            return f"accession:{clean_accession}"
        return f"metadata:{form}|{filing_date}|{document_url or ''}"

    existing_by_accession: dict[str, Filing] = {}
    existing_by_metadata: dict[str, Filing] = {}
    for existing in existing_filings:
        accession = (existing.accession_number or "").strip()
        if accession:
            existing_by_accession.setdefault(accession, existing)
        # Keep a metadata lookup even when an accession exists so a legacy row whose accession was
        # never populated can be matched and repaired by the next refresh.
        existing_by_metadata.setdefault(
            filing_key(
                None,
                existing.form_type,
                existing.filing_date,
                existing.document_url,
            ),
            existing,
        )
    tenk_filing: Filing | None = None
    for m in metas:
        accession = (m.accession or "").strip()
        metadata_key = filing_key(None, m.form, m.filing_date, m.primary_doc_url)
        filing = existing_by_accession.get(accession) if accession else None
        if filing is None:
            filing = existing_by_metadata.get(metadata_key)
        if filing is None:
            filing = Filing(
                workspace_id=workspace_id,
                company_name=name,
                ticker=info["ticker"],
                cik=cik,
                form_type=m.form,
                filing_date=m.filing_date,
                accession_number=m.accession or None,
                document_url=m.primary_doc_url or None,
                is_synthetic=False,
            )
            session.add(filing)
        else:
            # Refresh stale metadata in place. The accession is the immutable EDGAR identity.
            filing.company_name = name
            filing.ticker = info["ticker"]
            filing.cik = cik
            filing.form_type = m.form
            filing.filing_date = m.filing_date
            filing.accession_number = m.accession or filing.accession_number
            filing.document_url = m.primary_doc_url or filing.document_url
            filing.is_synthetic = False
        if accession:
            existing_by_accession[accession] = filing
        existing_by_metadata[metadata_key] = filing
        if m.form == "10-K" and tenk_filing is None:
            tenk_filing = filing
    session.flush()

    # --- 10-K sections -> chunks (for risk extraction / retrieval) ---
    business_text = ""
    if tenk_filing is None:
        latest = edgar_client.recent_filings(cik, ("10-K",), 1)
        if latest:
            # A 10-K exists but was outside the metadata window; reuse it or add it once.
            m = latest[0]
            accession = (m.accession or "").strip()
            metadata_key = filing_key(None, "10-K", m.filing_date, m.primary_doc_url)
            tenk_filing = existing_by_accession.get(accession) if accession else None
            if tenk_filing is None:
                tenk_filing = existing_by_metadata.get(metadata_key)
            if tenk_filing is None:
                tenk_filing = Filing(
                    workspace_id=workspace_id,
                    company_name=name,
                    ticker=info["ticker"],
                    cik=cik,
                    form_type="10-K",
                    filing_date=m.filing_date,
                    accession_number=m.accession or None,
                    document_url=m.primary_doc_url or None,
                    is_synthetic=False,
                )
                session.add(tenk_filing)
            else:
                tenk_filing.accession_number = m.accession or tenk_filing.accession_number
                tenk_filing.document_url = m.primary_doc_url or tenk_filing.document_url
            if accession:
                existing_by_accession[accession] = tenk_filing
            existing_by_metadata[metadata_key] = tenk_filing
            session.flush()

    if tenk_filing is not None and tenk_filing.document_url:
        stored_chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.filing_id == tenk_filing.id)
                .order_by(DocumentChunk.chunk_index, DocumentChunk.created_at)
            )
        )
        if stored_chunks:
            # The filing accession is immutable. Reuse its derived chunks and remove any legacy
            # duplicates produced by older refreshes instead of fetching/appending them again.
            unique_by_index: dict[tuple[str, int], DocumentChunk] = {}
            for chunk in stored_chunks:
                key = (chunk.section, chunk.chunk_index)
                if key in unique_by_index:
                    session.delete(chunk)
                    continue
                unique_by_index[key] = chunk
                chunk.source_url = tenk_filing.document_url
            kept_chunks = list(unique_by_index.values())
            tenk_filing.section_count = len(kept_chunks)
            business_text = " ".join(
                chunk.chunk_text
                for chunk in kept_chunks
                if chunk.section == "Business (Item 1)"
            )
            session.flush()
        else:
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
