"""Tests for Agent 4: news signals + filing-watch/refresh automations.

Offline unit tests cover the pure logic (query building, GDELT parsing/defensiveness, filing-watch
comparison, refresh wiring). Live tests hit real SEC/GDELT and are network-guarded via the shared
`live_workspace_id` fixture (skipped when SEC EDGAR is unreachable).

The signals router is exercised through a module-local FastAPI app so these tests do not depend on
the integration agent having wired the router into src/main.py yet; it shares the same SQLite DB.
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

    from src.routers import signals
    from src.services.common import NotFound

    app = FastAPI()

    @app.exception_handler(NotFound)
    async def _not_found(request, exc):  # pragma: no cover - trivial
        return JSONResponse(status_code=404, content={"detail": exc.message})

    app.include_router(signals.router)
    with TestClient(app) as c:
        yield c


# --- offline unit tests: news query + GDELT parsing/defensiveness ------------
def test_clean_company_trims_suffix():
    from src.services import news_service

    assert news_service._clean_company("MICROSOFT CORP") == "Microsoft"
    assert news_service._clean_company("APPLE INC") == "Apple"
    assert news_service._clean_company("Berkshire Hathaway Inc.") == "Berkshire Hathaway"


def test_build_query_quotes_multiword_only():
    from src.services import news_service

    assert news_service.build_query("MICROSOFT CORP") == "Microsoft sourcelang:english"
    q = news_service.build_query("Berkshire Hathaway Inc")
    assert q == '"Berkshire Hathaway" sourcelang:english'


def test_parse_articles_filters_incomplete_rows():
    from src.services import news_service

    data = {
        "articles": [
            {"title": "Real headline", "url": "https://ex.com/a", "domain": "ex.com",
             "seendate": "20260101T000000Z", "sourcecountry": "US"},
            {"title": "", "url": "https://ex.com/b"},          # no title -> dropped
            {"title": "No url", "url": ""},                       # no url -> dropped
            "not-a-dict",                                          # junk -> dropped
        ]
    }
    out = news_service._parse_articles(data)
    assert len(out) == 1
    a = out[0]
    assert a["title"] == "Real headline"
    assert a["url"] == "https://ex.com/a"
    assert a["sourcecountry"] == "US"


def test_parse_articles_handles_non_dict():
    from src.services import news_service

    assert news_service._parse_articles("garbage") == []
    assert news_service._parse_articles({"articles": None}) == []


def test_fetch_news_degrades_on_non_json(monkeypatch):
    """A 200 response with an HTML/plain-text body (GDELT error notice) -> empty, no raise."""
    from src.services import news_service

    class FakeResp:
        headers = {"content-type": "text/html"}
        text = "Your query was too short."

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not JSON")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(news_service.httpx, "Client", FakeClient)
    out = news_service.fetch_news("Microsoft Corp")
    assert out["articles"] == []
    assert out["query"] == "Microsoft sourcelang:english"


def test_fetch_news_parses_articles(monkeypatch):
    from src.services import news_service

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"articles": [
                {"title": "Deal news", "url": "https://news.ex/1", "domain": "news.ex",
                 "seendate": "20260701T120000Z", "sourcecountry": "US"},
            ]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(news_service.httpx, "Client", FakeClient)
    out = news_service.fetch_news("Acme Widgets")
    assert len(out["articles"]) == 1
    assert out["articles"][0]["domain"] == "news.ex"


# --- offline unit tests: filing-watch comparison + refresh wiring ------------
def test_filing_watch_detects_new(monkeypatch):
    from src.services import edgar_client, watch_service, workspace_service

    target = SimpleNamespace(cik="0000789019", ticker="MSFT", name="MICROSOFT CORP")
    monkeypatch.setattr(workspace_service, "get_target", lambda s, w: target)

    stored = [
        SimpleNamespace(accession_number="acc-1", filing_date="2025-01-15"),
        SimpleNamespace(accession_number="acc-2", filing_date="2025-03-20"),
    ]
    monkeypatch.setattr(watch_service, "_stored_filings", lambda s, w: stored)

    FM = edgar_client.FilingMeta

    def fake_recent(cik, forms, limit):
        return [
            FM("8-K", "2025-05-01", "acc-9", "d9.htm", "https://x/9", "2025-05-01"),   # new
            FM("10-Q", "2025-03-20", "acc-2", "d2.htm", "https://x/2", "2025-03-20"),  # stored
            FM("8-K", "2025-01-15", "acc-1", "d1.htm", "https://x/1", "2025-01-15"),   # old
        ]

    monkeypatch.setattr(edgar_client, "recent_filings", fake_recent)

    res = watch_service.filing_watch(object(), "ws1")
    assert res["last_ingested_date"] == "2025-03-20"
    assert res["has_new"] is True
    assert [f["accession"] for f in res["new_filings"]] == ["acc-9"]
    assert res["new_filings"][0]["url"] == "https://x/9"


def test_filing_watch_none_when_up_to_date(monkeypatch):
    from src.services import edgar_client, watch_service, workspace_service

    target = SimpleNamespace(cik="0000789019", ticker="MSFT", name="MICROSOFT CORP")
    monkeypatch.setattr(workspace_service, "get_target", lambda s, w: target)
    stored = [SimpleNamespace(accession_number="acc-2", filing_date="2025-03-20")]
    monkeypatch.setattr(watch_service, "_stored_filings", lambda s, w: stored)

    FM = edgar_client.FilingMeta
    monkeypatch.setattr(
        edgar_client, "recent_filings",
        lambda c, f, l: [FM("10-Q", "2025-03-20", "acc-2", "d2.htm", "https://x/2", "2025-03-20")],
    )
    res = watch_service.filing_watch(object(), "ws1")
    assert res["has_new"] is False
    assert res["new_filings"] == []


def test_filing_watch_requires_cik(monkeypatch):
    from src.services import watch_service, workspace_service
    from src.services.common import NotFound

    monkeypatch.setattr(workspace_service, "get_target", lambda s, w: None)
    with pytest.raises(NotFound):
        watch_service.filing_watch(object(), "ws1")


def test_refresh_wired(monkeypatch):
    """refresh must re-ingest, commit, re-run analysis, and return the overview."""
    from src.services import (
        analysis_service,
        sec_ingestion_service,
        watch_service,
        workspace_service,
    )

    calls: list = []
    target = SimpleNamespace(ticker="MSFT", cik="0000789019", name="MICROSOFT CORP")
    monkeypatch.setattr(workspace_service, "get_target", lambda s, w: target)
    monkeypatch.setattr(
        sec_ingestion_service, "ingest_company",
        lambda s, w, t: calls.append(("ingest", w, t)),
    )
    monkeypatch.setattr(
        analysis_service, "run_full_analysis",
        lambda s, w: calls.append(("analyze", w)),
    )
    monkeypatch.setattr(
        workspace_service, "get_overview",
        lambda s, w: {"overview_for": w},
    )

    class FakeSession:
        def commit(self):
            calls.append(("commit",))

    result = watch_service.refresh(FakeSession(), "ws1")
    assert result == {"overview_for": "ws1"}
    assert ("ingest", "ws1", "MSFT") in calls
    assert ("analyze", "ws1") in calls
    assert ("commit",) in calls
    # ingest + commit must precede analysis.
    assert calls.index(("commit",)) < calls.index(("analyze", "ws1"))


# --- live integration tests (network-guarded via live_workspace_id) ----------
def test_filing_watch_live_shape(signals_client, live_workspace_id):
    fw = signals_client.get(f"/api/workspaces/{live_workspace_id}/filing-watch")
    assert fw.status_code == 200, fw.text
    body = fw.json()
    assert body["workspace_id"] == live_workspace_id
    assert isinstance(body["has_new"], bool)
    assert isinstance(body["new_filings"], list)
    assert body["last_ingested_date"] is None or isinstance(body["last_ingested_date"], str)
    for nf in body["new_filings"]:
        assert nf["form"] and nf["date"]
        assert set(nf) == {"form", "date", "accession", "url"}
    assert body["generated_at"]


def test_news_live_tolerates_empty(signals_client, live_workspace_id):
    resp = signals_client.get(f"/api/workspaces/{live_workspace_id}/news")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == live_workspace_id
    assert body["query"]  # a real query string is always built
    assert isinstance(body["articles"], list)  # may be empty if GDELT is flaky
    for a in body["articles"]:
        assert a["title"] and a["url"]
        assert "domain" in a and "seendate" in a


def test_filing_watch_missing_workspace_404(signals_client):
    resp = signals_client.get("/api/workspaces/does-not-exist/filing-watch")
    assert resp.status_code == 404
