"""G19 — watchlists with scheduled refresh.

Covers add/list/remove, the refresh scheduler detecting a new filing and emitting exactly ONE
outbox event, dedup (a re-run with no new filing emits nothing), that the emitted audit event maps
to an in-app notification via notification_service, and cross-organization isolation. EDGAR
submissions are mocked for offline determinism (mirroring how test_signals.py fakes SEC data).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.db.base import Base
from src.models.deal_workflow import Organization, WorkflowAuditEvent
from src.services import edgar_client, notification_service
from src.services import watchlist_service as service


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _org(db: Session, slug: str = "watch-org") -> Organization:
    org = Organization(name=f"Org {slug}", slug=slug)
    db.add(org)
    db.commit()
    return org


def _submissions(rows: list[tuple[str, str, str, str]]) -> dict:
    """Build a submissions payload (newest-first) from (form, date, accession, doc) tuples."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "accessionNumber": [r[2] for r in rows],
                "primaryDocument": [r[3] for r in rows],
            }
        }
    }


def _patch_submissions(monkeypatch, holder: dict):
    """Point edgar_client.get_submissions at a mutable holder so tests can evolve the feed."""
    monkeypatch.setattr(
        edgar_client, "get_submissions", lambda cik10: holder["value"]
    )


def _watchlist_event_count(db: Session, organization_id: str) -> int:
    return db.scalar(
        select(func.count())
        .select_from(WorkflowAuditEvent)
        .where(
            WorkflowAuditEvent.organization_id == organization_id,
            WorkflowAuditEvent.action == service.WATCHLIST_EVENT,
        )
    )


# --- add / list / remove -----------------------------------------------------
def test_add_list_remove_entry_by_cik(db: Session):
    org = _org(db)
    entry = service.add_entry(
        db, org.id, cik="789019", company_name="Microsoft Corp", created_by="analyst-1"
    )
    assert entry.cik == "0000789019"  # normalized to 10 digits
    assert entry.company_name == "Microsoft Corp"
    assert entry.active is True
    assert entry.created_by == "analyst-1"

    listed = service.list_entries(db, org.id)
    assert [e.id for e in listed] == [entry.id]

    removed = service.remove_entry(db, entry.id, org.id)
    assert removed is not None
    assert service.list_entries(db, org.id) == []


def test_add_by_ticker_resolves_cik(db: Session, monkeypatch):
    org = _org(db)
    monkeypatch.setattr(
        edgar_client,
        "resolve_ticker",
        lambda t: {"ticker": "MSFT", "cik": "0000789019", "name": "MICROSOFT CORP"},
    )
    entry = service.add_entry(db, org.id, ticker="msft")
    assert entry.ticker == "MSFT"
    assert entry.cik == "0000789019"
    assert entry.company_name == "MICROSOFT CORP"


def test_add_is_idempotent_and_reactivates(db: Session):
    org = _org(db)
    first = service.add_entry(db, org.id, cik="789019", company_name="Microsoft Corp")
    service.remove_entry(db, first.id, org.id)  # gone

    # Re-add the same CIK: a fresh row (previous was deleted), still one entry.
    again = service.add_entry(db, org.id, cik="0000789019", company_name="Microsoft Corp")
    assert again.active is True
    assert len(service.list_entries(db, org.id)) == 1


def test_add_without_identifier_is_rejected(db: Session):
    org = _org(db)
    with pytest.raises(service.WatchlistError):
        service.add_entry(db, org.id, company_name="No Ticker Or CIK")


# --- refresh: detection, single emission, dedup ------------------------------
def test_refresh_baseline_then_detects_one_new_filing_then_dedups(db: Session, monkeypatch):
    org = _org(db)
    service.add_entry(db, org.id, cik="789019", company_name="Microsoft Corp")

    holder = {"value": _submissions([("10-Q", "2026-01-15", "acc-1", "d1.htm")])}
    _patch_submissions(monkeypatch, holder)

    # First refresh only establishes a baseline — an existing backlog must not flood events.
    baseline = service.refresh_watchlist(db, org.id)
    assert baseline["entries_checked"] == 1
    assert baseline["events_emitted"] == 0
    assert _watchlist_event_count(db, org.id) == 0

    # A brand-new filing appears (newest-first) -> exactly ONE outbox event.
    holder["value"] = _submissions(
        [("8-K", "2026-02-01", "acc-2", "d2.htm"), ("10-Q", "2026-01-15", "acc-1", "d1.htm")]
    )
    detected = service.refresh_watchlist(db, org.id)
    assert detected["new_filings"] == 1
    assert detected["events_emitted"] == 1
    assert _watchlist_event_count(db, org.id) == 1

    # Re-running with no new filing emits nothing (dedup on last_seen_accession).
    dedup = service.refresh_watchlist(db, org.id)
    assert dedup["events_emitted"] == 0
    assert _watchlist_event_count(db, org.id) == 1


def test_refresh_emits_multiple_new_filings_oldest_first(db: Session, monkeypatch):
    org = _org(db)
    service.add_entry(db, org.id, cik="789019", company_name="Microsoft Corp")

    holder = {"value": _submissions([("10-Q", "2026-01-15", "acc-1", "d1.htm")])}
    _patch_submissions(monkeypatch, holder)
    service.refresh_watchlist(db, org.id)  # baseline acc-1

    holder["value"] = _submissions(
        [
            ("8-K", "2026-03-01", "acc-3", "d3.htm"),
            ("8-K", "2026-02-01", "acc-2", "d2.htm"),
            ("10-Q", "2026-01-15", "acc-1", "d1.htm"),
        ]
    )
    result = service.refresh_watchlist(db, org.id)
    assert result["events_emitted"] == 2

    events = list(
        db.scalars(
            select(WorkflowAuditEvent)
            .where(WorkflowAuditEvent.action == service.WATCHLIST_EVENT)
            .order_by(WorkflowAuditEvent.created_at, WorkflowAuditEvent.id)
        )
    )
    # Chronological: older accession emitted before the newer one.
    assert [e.detail["accession"] for e in events] == ["acc-2", "acc-3"]


def test_refresh_edgar_outage_emits_nothing_and_marks_unavailable(db: Session, monkeypatch):
    org = _org(db)
    service.add_entry(db, org.id, cik="789019", company_name="Microsoft Corp")

    def boom(cik10):
        raise edgar_client.EdgarError("offline")

    monkeypatch.setattr(edgar_client, "get_submissions", boom)
    result = service.refresh_watchlist(db, org.id)
    assert result["unavailable"] == 1
    assert result["events_emitted"] == 0
    assert _watchlist_event_count(db, org.id) == 0


# --- notification mapping -----------------------------------------------------
def test_emitted_event_maps_to_a_notification(db: Session, monkeypatch):
    org = _org(db)
    service.add_entry(db, org.id, cik="789019", company_name="Microsoft Corp")

    holder = {"value": _submissions([("10-Q", "2026-01-15", "acc-1", "d1.htm")])}
    _patch_submissions(monkeypatch, holder)
    service.refresh_watchlist(db, org.id)  # baseline
    holder["value"] = _submissions(
        [("8-K", "2026-02-01", "acc-2", "d2.htm"), ("10-Q", "2026-01-15", "acc-1", "d1.htm")]
    )
    service.refresh_watchlist(db, org.id)  # emits one audit event

    created = notification_service.sync_from_audit(db, org.id)
    watch_notes = [n for n in created if n.event_type == service.WATCHLIST_EVENT]
    assert len(watch_notes) == 1
    assert watch_notes[0].title == "New filing detected"
    assert watch_notes[0].source_audit_event_id

    # Idempotent: a second sync creates nothing new (outbox dedup by source_audit_event_id).
    assert notification_service.sync_from_audit(db, org.id) == []


# --- cross-org isolation ------------------------------------------------------
def test_refresh_is_cross_org_isolated(db: Session, monkeypatch):
    org_a = _org(db, "org-a")
    org_b = _org(db, "org-b")
    service.add_entry(db, org_a.id, cik="789019", company_name="Alpha Co")
    service.add_entry(db, org_b.id, cik="320193", company_name="Beta Co")

    holder = {"value": _submissions([("10-Q", "2026-01-15", "acc-1", "d1.htm")])}
    _patch_submissions(monkeypatch, holder)

    # Baseline both, then a new filing appears, but we only refresh org A.
    service.refresh_watchlist(db, org_a.id)
    service.refresh_watchlist(db, org_b.id)
    holder["value"] = _submissions(
        [("8-K", "2026-02-01", "acc-2", "d2.htm"), ("10-Q", "2026-01-15", "acc-1", "d1.htm")]
    )
    service.refresh_watchlist(db, org_a.id)

    assert _watchlist_event_count(db, org_a.id) == 1
    assert _watchlist_event_count(db, org_b.id) == 0
    # org A's entries are untouched by refreshing only org A; org B never sees org A's events.
    assert notification_service.sync_from_audit(db, org_b.id) == []


def test_refresh_all_covers_every_org_with_active_entries(db: Session, monkeypatch):
    org_a = _org(db, "all-a")
    org_b = _org(db, "all-b")
    service.add_entry(db, org_a.id, cik="789019", company_name="Alpha Co")
    service.add_entry(db, org_b.id, cik="320193", company_name="Beta Co")

    holder = {"value": _submissions([("10-Q", "2026-01-15", "acc-1", "d1.htm")])}
    _patch_submissions(monkeypatch, holder)
    totals = service.refresh_all(db)
    assert totals["organizations"] == 2
    assert totals["entries_checked"] == 2
