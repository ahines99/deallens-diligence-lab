"""G19 — watchlists with scheduled refresh.

Track N companies per organization; on refresh, read EDGAR submissions for each active entry,
detect filings newer than the ``last_seen_accession`` dedup cursor, and emit one
``WorkflowAuditEvent`` per new filing through the existing outbox. Those events are consumed by
``notification_service`` (in-app notifications) and ``webhook_service`` (signed webhook deliveries),
so a new filing surfaces everywhere the audit stream already fans out to.

Dedup is the core contract: a filing already recorded as ``last_seen_accession`` is never
re-emitted. The first refresh of a new entry only establishes the baseline (newest accession)
without emitting, so an existing filing backlog never floods notifications.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import Organization, WorkflowAuditEvent
from src.models.watchlist import WatchlistEntry
from src.services import edgar_client, webhook_service
from src.services.edgar_client import EdgarError

logger = logging.getLogger("deallens.watchlist")

WATCHLIST_EVENT = "watchlist.filing_detected"
_RECENT_LIMIT = 25  # most-recent filings scanned per entry on each refresh


class WatchlistError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _cik10(cik: str) -> str:
    return str(cik).strip().lstrip("0").zfill(10) if cik else ""


def _resolve_identity(
    ticker: str | None, cik: str | None, company_name: str | None
) -> tuple[str | None, str, str]:
    """Return (ticker, cik10, company_name), resolving a ticker to a CIK via EDGAR when given."""
    if ticker:
        try:
            info = edgar_client.resolve_ticker(ticker)
        except EdgarError as exc:
            raise WatchlistError(f"Ticker '{ticker}' could not be resolved: {exc}") from exc
        return info["ticker"], _cik10(info["cik"]), company_name or info["name"]
    cik10 = _cik10(cik or "")
    if not cik10 or cik10 == "0000000000":
        raise WatchlistError("A valid ticker or CIK is required to watch a company.")
    return None, cik10, company_name or f"CIK {cik10}"


def add_entry(
    session: Session,
    organization_id: str,
    *,
    ticker: str | None = None,
    cik: str | None = None,
    company_name: str | None = None,
    created_by: str | None = None,
) -> WatchlistEntry:
    """Add (or re-activate) a watchlist entry for a company, keyed by (organization, CIK)."""
    resolved_ticker, resolved_cik, resolved_name = _resolve_identity(ticker, cik, company_name)
    existing = session.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.organization_id == organization_id,
            WatchlistEntry.cik == resolved_cik,
        )
    )
    if existing is not None:
        # Idempotent: re-adding a watched (or previously removed) company reactivates it in place.
        changed = False
        if not existing.active:
            existing.active = True
            changed = True
        if resolved_ticker and existing.ticker != resolved_ticker:
            existing.ticker = resolved_ticker
            changed = True
        if changed:
            session.commit()
            session.refresh(existing)
        return existing
    entry = WatchlistEntry(
        organization_id=organization_id,
        ticker=resolved_ticker,
        cik=resolved_cik,
        company_name=resolved_name,
        created_by=created_by,
        active=True,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def list_entries(session: Session, organization_id: str) -> list[WatchlistEntry]:
    return list(
        session.scalars(
            select(WatchlistEntry)
            .where(WatchlistEntry.organization_id == organization_id)
            .order_by(WatchlistEntry.created_at.desc(), WatchlistEntry.id)
        )
    )


def remove_entry(
    session: Session, entry_id: str, organization_id: str | None = None
) -> WatchlistEntry | None:
    """Delete a watchlist entry, scoped to its organization when the caller's tenant is known."""
    entry = session.get(WatchlistEntry, entry_id)
    if entry is None or (organization_id is not None and entry.organization_id != organization_id):
        return None
    session.delete(entry)
    session.commit()
    return entry


def _recent_filings(cik10: str) -> list[dict] | None:
    """Return recent filings (newest-first) as plain dicts, or None on an EDGAR outage."""
    try:
        recent = edgar_client.get_submissions(cik10).get("filings", {}).get("recent", {})
    except EdgarError as exc:
        logger.warning("watchlist refresh: submissions fetch failed for %s: %s", cik10, exc)
        return None
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cik_int = int(cik10)
    rows: list[dict] = []
    for i, form in enumerate(forms):
        if i >= _RECENT_LIMIT:
            break
        acc = accs[i] if i < len(accs) else ""
        if not acc:
            continue
        doc = docs[i] if i < len(docs) else ""
        acc_nodash = acc.replace("-", "")
        url = (
            edgar_client.ARCHIVES.format(cik=cik_int, acc=acc_nodash, doc=doc)
            if acc_nodash and doc
            else None
        )
        rows.append(
            {
                "form": form,
                "date": dates[i] if i < len(dates) else "",
                "accession": acc,
                "url": url,
            }
        )
    return rows


def _detect_new(recent: list[dict], last_seen: str) -> list[dict]:
    """Filings newer than ``last_seen`` (walking newest-first, stopping at the first seen one)."""
    new: list[dict] = []
    for filing in recent:
        if filing["accession"] == last_seen:
            break
        new.append(filing)
    return new


def _emit_new_filing_event(
    session: Session, entry: WatchlistEntry, filing: dict
) -> WorkflowAuditEvent:
    """Append one audit event for a newly detected filing and fan it into the webhook outbox."""
    event = WorkflowAuditEvent(
        organization_id=entry.organization_id,
        deal_id=None,
        actor_id=None,
        actor_display_name="Watchlist monitor",
        action=WATCHLIST_EVENT,
        entity_type="WatchlistEntry",
        entity_id=entry.id,
        detail={
            "company_name": entry.company_name,
            "ticker": entry.ticker,
            "cik": entry.cik,
            "form": filing["form"],
            "filing_date": filing["date"],
            "accession": filing["accession"],
            "url": filing["url"],
        },
        request_id=None,
    )
    session.add(event)
    session.flush()
    # Fan into the durable, HMAC-signed webhook outbox. Notifications are drained separately by
    # notification_service.sync_from_audit (same append-only audit stream, idempotent consumer).
    webhook_service.queue_for_audit_event(session, event)
    return event


def _claim_cursor(
    session: Session, entry: WatchlistEntry, expected: str | None, newest: str
) -> bool:
    """Atomically advance ``last_seen_accession`` from ``expected`` to ``newest``.

    Returns False when a concurrent refresher already moved the cursor — the caller must not
    emit for this entry. Mirrors the job queue's atomic-claim rowcount pattern.
    """
    result = session.execute(
        update(WatchlistEntry)
        .where(
            WatchlistEntry.id == entry.id,
            WatchlistEntry.last_seen_accession.is_(None)
            if expected is None
            else WatchlistEntry.last_seen_accession == expected,
        )
        .values(last_seen_accession=newest)
        .execution_options(synchronize_session=False)
    )
    # The raw UPDATE bypassed the identity map; expire the attribute so a later refresh on
    # this session reloads the claimed cursor instead of a stale value.
    session.expire(entry, ["last_seen_accession"])
    return result.rowcount == 1


def refresh_watchlist(session: Session, organization_id: str) -> dict:
    """Check every active entry for this org, emitting one outbox event per new filing.

    Dedup: filings at or before ``last_seen_accession`` are skipped; a brand-new entry only
    records a baseline on its first refresh and emits nothing.
    """
    entries = list(
        session.scalars(
            select(WatchlistEntry).where(
                WatchlistEntry.organization_id == organization_id,
                WatchlistEntry.active.is_(True),
            )
        )
    )
    checked = new_filings = emitted = unavailable = 0
    for entry in entries:
        recent = _recent_filings(entry.cik)
        entry.last_checked_at = now_utc()
        if recent is None:
            unavailable += 1
            continue
        checked += 1
        if not recent:
            continue
        newest = recent[0]["accession"]
        if entry.last_seen_accession is None:
            # First observation: establish the baseline without flooding the existing backlog.
            # Compare-and-set so a concurrent first refresh cannot clobber the baseline.
            _claim_cursor(session, entry, None, newest)
            continue
        detected = _detect_new(recent, entry.last_seen_accession)
        if not detected:
            continue
        # Advance the cursor with a compare-and-set BEFORE emitting: a plain read-modify-write
        # let two concurrent refreshes (worker + user-triggered) detect the same filings and
        # emit duplicate webhook/notification events. The loser of the claim emits nothing,
        # and a failure while emitting rolls the claim back with the same transaction.
        if not _claim_cursor(session, entry, entry.last_seen_accession, newest):
            continue
        # Emit oldest-first so the audit/notification stream reads chronologically.
        for filing in reversed(detected):
            _emit_new_filing_event(session, entry, filing)
            new_filings += 1
            emitted += 1
    session.commit()
    return {
        "organization_id": organization_id,
        "entries_checked": checked,
        "new_filings": new_filings,
        "events_emitted": emitted,
        "unavailable": unavailable,
    }


def refresh_all(session: Session) -> dict:
    """Refresh every organization that has at least one active watchlist entry (worker entrypoint)."""
    org_ids = list(
        session.scalars(
            select(Organization.id)
            .join(WatchlistEntry, WatchlistEntry.organization_id == Organization.id)
            .where(WatchlistEntry.active.is_(True))
            .distinct()
        )
    )
    totals = {
        "organizations": len(org_ids),
        "entries_checked": 0,
        "new_filings": 0,
        "events_emitted": 0,
        "unavailable": 0,
    }
    for org_id in org_ids:
        result = refresh_watchlist(session, org_id)
        totals["entries_checked"] += result["entries_checked"]
        totals["new_filings"] += result["new_filings"]
        totals["events_emitted"] += result["events_emitted"]
        totals["unavailable"] += result["unavailable"]
    return totals
