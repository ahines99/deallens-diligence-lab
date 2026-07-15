"""G18 — consolidated signals overview (carryover F55).

Aggregation is tested offline by faking each feed service: the overview must carry each section's
OWN source_status (so an unavailable feed shows `unavailable`, never a clean-empty merge) and roll
those up honestly into overall_status. A module-local FastAPI app exercises the endpoint contract,
mirroring test_signals.py so the tests do not depend on router wiring elsewhere.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# --- module-local app that mounts the signals router (shares the test DB) ----
@pytest.fixture(scope="module")
def signals_client():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from src.db.session import prepare_schema
    from src.routers import signals
    from src.services.common import NotFound

    # The module-local app has no lifespan, so ensure the shared test DB schema exists (the real
    # app does this via prepare_schema() on startup); idempotent under create_all.
    prepare_schema()
    app = FastAPI()

    @app.exception_handler(NotFound)
    async def _not_found(request, exc):  # pragma: no cover - trivial
        return JSONResponse(status_code=404, content={"detail": exc.message})

    app.include_router(signals.router)
    with TestClient(app) as c:
        yield c


def _fake_feeds(monkeypatch, *, events_status="available", themes_status="available",
                insiders_status="available", news_status="available"):
    """Stub every underlying feed the overview aggregates, with controllable per-source status."""
    from src.services import news_service, sec_feeds_service, signals_overview_service

    target = SimpleNamespace(cik="0000789019", ticker="MSFT", name="MICROSOFT CORP")
    monkeypatch.setattr(
        signals_overview_service.workspace_service, "get_target", lambda s, w: target
    )

    def fake_events(session, ws):
        return {
            "workspace_id": ws,
            "events": [
                {"date": "2026-01-02", "form": "8-K", "items": [{"code": "4.02", "label": "x"}],
                 "accession": "a1", "url": None, "significant": True},
                {"date": "2026-01-01", "form": "10-Q", "items": [], "accession": "a2",
                 "url": None, "significant": False},
            ] if events_status != "unavailable" else [],
            "source_status": events_status,
            "source_error": None if events_status == "available" else "events down",
            "generated_at": "t",
        }

    def fake_insiders(session, ws):
        return {
            "workspace_id": ws,
            "summary": {"buys": 1, "sells": 2, "net_shares": -10.0, "window_days": 90}
            if insiders_status != "unavailable"
            else {"buys": None, "sells": None, "net_shares": None, "window_days": 90},
            "transactions": [{"type": "sell", "shares": 10.0}] if insiders_status != "unavailable" else [],
            "source_status": insiders_status,
            "source_error": None if insiders_status == "available" else "insiders down",
            "generated_at": "t",
        }

    def fake_themes(session, ws):
        return {
            "workspace_id": ws,
            "themes": [
                {"theme": "going_concern", "label": "Going concern",
                 "count": 3 if themes_status != "unavailable" else None, "hits": []},
                {"theme": "restatement", "label": "Restatement",
                 "count": 0 if themes_status != "unavailable" else None, "hits": []},
            ],
            "source_status": themes_status,
            "source_error": None if themes_status == "available" else "themes down",
            "generated_at": "t",
        }

    def fake_news(company, max_records=15):
        return {
            "query": company,
            "articles": [{"title": "Deal news", "url": "https://n/1", "domain": "n",
                          "seendate": "20260101T000000Z", "sourcecountry": "US"}]
            if news_status == "available" else [],
            "source_status": news_status,
            "source_error": None if news_status == "available" else "news down",
        }

    monkeypatch.setattr(sec_feeds_service, "events", fake_events)
    monkeypatch.setattr(sec_feeds_service, "insiders", fake_insiders)
    monkeypatch.setattr(sec_feeds_service, "themes", fake_themes)
    monkeypatch.setattr(news_service, "fetch_news", fake_news)
    return target


def test_overview_aggregates_four_sections_each_with_source_status(monkeypatch):
    from src.services import signals_overview_service

    _fake_feeds(monkeypatch)
    result = signals_overview_service.overview(object(), "ws1")

    kinds = [s["kind"] for s in result["sections"]]
    assert kinds == ["events", "insiders", "themes", "news"]
    assert all(s["source_status"] == "available" for s in result["sections"])
    assert result["overall_status"] == "available"
    assert result["workspace_id"] == "ws1"

    by_kind = {s["kind"]: s for s in result["sections"]}
    assert by_kind["events"]["summary"] == {"total": 2, "significant": 1}
    assert by_kind["insiders"]["summary"]["sells"] == 2
    assert by_kind["themes"]["summary"] == {"total_hits": 3, "flagged": 1}
    assert by_kind["news"]["summary"] == {"total": 1}


def test_unavailable_feed_shows_unavailable_not_clean_empty(monkeypatch):
    """A degraded feed must surface `unavailable`, and the roll-up must go `partial` — never a
    false-clean `available` just because the other sections happened to be empty-but-ok."""
    from src.services import signals_overview_service

    _fake_feeds(monkeypatch, themes_status="unavailable")
    result = signals_overview_service.overview(object(), "ws1")

    themes = next(s for s in result["sections"] if s["kind"] == "themes")
    assert themes["source_status"] == "unavailable"
    assert themes["source_error"] == "themes down"
    assert themes["summary"]["total_hits"] is None  # NOT 0 — the count is unknown, not clean-zero
    assert result["overall_status"] == "partial"


def test_overall_status_is_unavailable_only_when_every_feed_is_down(monkeypatch):
    from src.services import signals_overview_service

    _fake_feeds(
        monkeypatch,
        events_status="unavailable",
        insiders_status="unavailable",
        themes_status="unavailable",
        news_status="unavailable",
    )
    result = signals_overview_service.overview(object(), "ws1")
    assert all(s["source_status"] == "unavailable" for s in result["sections"])
    assert result["overall_status"] == "unavailable"


def test_no_target_raises_not_found(monkeypatch):
    from src.services import signals_overview_service
    from src.services.common import NotFound

    monkeypatch.setattr(
        signals_overview_service.workspace_service, "get_target", lambda s, w: None
    )
    with pytest.raises(NotFound):
        signals_overview_service.overview(object(), "ws1")


def test_signals_overview_endpoint_contract(signals_client, monkeypatch):
    """The HTTP surface renders the consolidated overview for a real workspace."""
    from src.db.session import SessionLocal
    from src.models.workspace import Workspace

    _fake_feeds(monkeypatch)
    with SessionLocal() as session:
        ws = Workspace(name="Overview WS")
        session.add(ws)
        session.commit()
        workspace_id = ws.id

    resp = signals_client.get(f"/api/workspaces/{workspace_id}/signals-overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == workspace_id
    assert {s["kind"] for s in body["sections"]} == {"events", "insiders", "themes", "news"}
    assert body["overall_status"] in {"available", "partial", "unavailable"}
    for section in body["sections"]:
        assert section["source_status"] in {"available", "partial", "unavailable"}
        assert "summary" in section and "items" in section
    assert body["generated_at"]


def test_signals_overview_missing_workspace_404(signals_client):
    resp = signals_client.get("/api/workspaces/does-not-exist/signals-overview")
    assert resp.status_code == 404
