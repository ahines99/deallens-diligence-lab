"""Automations: filing-watch and workspace refresh.

filing_watch: compares the latest EDGAR filings for the target against what's already stored in the
workspace so an analyst can see, at a glance, whether the company has filed anything new since
ingestion (a fresh 8-K, 10-Q, or 10-K).

refresh: re-ingests the latest filings for the target's ticker and re-runs the full analysis
pipeline, then returns the updated workspace overview. Only refresh mutates state.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models import Filing
from src.services import edgar_client, sec_ingestion_service, workspace_service
from src.services.common import NotFound
from src.services.edgar_client import EdgarError

logger = logging.getLogger("deallens.watch")

WATCH_FORMS = ("10-K", "10-Q", "8-K")
WATCH_LIMIT = 15


def _stored_filings(session: Session, workspace_id: str) -> list[Filing]:
    return list(
        session.scalars(select(Filing).where(Filing.workspace_id == workspace_id))
    )


def filing_watch(session: Session, workspace_id: str) -> dict:
    """Compare live EDGAR filings against stored ones → has_new + the newer filings."""
    target = workspace_service.get_target(session, workspace_id)
    if target is None or not target.cik:
        raise NotFound("No target with a CIK for this workspace; ingest a public company first.")

    stored = _stored_filings(session, workspace_id)
    stored_acc = {f.accession_number for f in stored if f.accession_number}
    stored_dates = [f.filing_date for f in stored if f.filing_date]
    # 'YYYY-MM-DD' strings sort lexicographically == chronologically.
    last_ingested = max(stored_dates) if stored_dates else None

    def _shape(
        new_filings: list[dict],
        *,
        source_status: str = "available",
        source_error: str | None = None,
    ) -> dict:
        return {
            "workspace_id": workspace_id,
            "last_ingested_date": last_ingested,
            "has_new": bool(new_filings) if source_status == "available" else None,
            "new_filings": new_filings,
            "source_status": source_status,
            "source_error": source_error,
            "generated_at": now_utc(),
        }

    try:
        metas = edgar_client.recent_filings(target.cik, WATCH_FORMS, WATCH_LIMIT)
    except EdgarError as exc:
        logger.warning("filing-watch fetch failed for CIK %s: %s", target.cik, exc)
        return _shape(
            [],
            source_status="unavailable",
            source_error="SEC EDGAR filing watch is temporarily unavailable.",
        )

    new_filings: list[dict] = []
    for m in metas:
        # "New" = filed after the newest stored filing date, and not already stored by accession.
        newer = last_ingested is None or (m.filing_date and m.filing_date > last_ingested)
        if newer and m.accession not in stored_acc:
            new_filings.append(
                {
                    "form": m.form,
                    "date": m.filing_date,
                    "accession": m.accession or None,
                    "url": m.primary_doc_url or None,
                }
            )
    return _shape(new_filings)


def refresh(session: Session, workspace_id: str) -> dict:
    """Re-ingest the target's latest filings, re-run full analysis, return the fresh overview."""
    target = workspace_service.get_target(session, workspace_id)
    if target is None or not target.ticker:
        raise NotFound("No target with a ticker for this workspace; ingest a public company first.")

    sec_ingestion_service.ingest_company(session, workspace_id, target.ticker)
    session.commit()

    # Imported lazily: we call analysis_service (not edit it), and avoid an import cycle.
    from src.services import analysis_service

    analysis_service.run_full_analysis(session, workspace_id)  # commits internally
    return workspace_service.get_overview(session, workspace_id)
