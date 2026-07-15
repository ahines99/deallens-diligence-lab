"""G18 — consolidated signals overview (carryover F55).

One screen that aggregates the SEC event / insider / theme feeds and the GDELT news feed. Each
section carries its OWN ``source_status`` (available / partial / unavailable) plus a matching
``source_error``, so a degraded or offline feed is shown explicitly and is never silently merged
into a false-clean empty result. ``overall_status`` is the honest roll-up of the section statuses.

Pure aggregation over the existing feed services — no new network access is introduced here.
"""
from __future__ import annotations

from src.db.base import now_utc
from src.services import news_service, sec_feeds_service, workspace_service
from src.services.common import NotFound

# Items carried per section are already bounded by each feed, but cap defensively so the overview
# stays a compact "one screen" payload rather than a full dump of every source.
_ITEM_CAP = 25


def _section(
    kind: str, source_status: str, source_error: str | None, summary: dict, items: list
) -> dict:
    return {
        "kind": kind,
        "source_status": source_status,
        "source_error": source_error,
        "summary": summary,
        "items": items[:_ITEM_CAP],
    }


def _events_section(session, workspace_id: str) -> dict:
    try:
        data = sec_feeds_service.events(session, workspace_id)
    except NotFound as exc:
        return _section("events", "unavailable", exc.message, {"total": 0, "significant": 0}, [])
    rows = data["events"]
    summary = {"total": len(rows), "significant": sum(1 for r in rows if r.get("significant"))}
    return _section("events", data["source_status"], data["source_error"], summary, rows)


def _insiders_section(session, workspace_id: str) -> dict:
    try:
        data = sec_feeds_service.insiders(session, workspace_id)
    except NotFound as exc:
        return _section(
            "insiders",
            "unavailable",
            exc.message,
            {"buys": None, "sells": None, "net_shares": None},
            [],
        )
    s = data["summary"]
    summary = {"buys": s["buys"], "sells": s["sells"], "net_shares": s["net_shares"]}
    return _section("insiders", data["source_status"], data["source_error"], summary, data["transactions"])


def _themes_section(session, workspace_id: str) -> dict:
    try:
        data = sec_feeds_service.themes(session, workspace_id)
    except NotFound as exc:
        return _section("themes", "unavailable", exc.message, {"total_hits": None, "flagged": 0}, [])
    rows = data["themes"]
    counts = [r["count"] for r in rows if r.get("count") is not None]
    # counts is empty only when every theme count is None (the feed is unavailable): report the
    # total as unknown (None) rather than a false-clean zero. An available feed with genuinely no
    # hits yields [0, 0, ...], so the sum is a truthful 0.
    summary = {
        "total_hits": sum(counts) if counts else None,
        "flagged": sum(1 for c in counts if c > 0),
    }
    return _section("themes", data["source_status"], data["source_error"], summary, rows)


def _news_section(company: str) -> dict:
    data = news_service.fetch_news(company)
    articles = data["articles"]
    return _section("news", data["source_status"], data["source_error"], {"total": len(articles)}, articles)


def _overall_status(statuses: list[str]) -> str:
    """Roll section statuses up honestly: clean only when EVERY feed is available."""
    if all(s == "available" for s in statuses):
        return "available"
    if all(s == "unavailable" for s in statuses):
        return "unavailable"
    return "partial"


def overview(session, workspace_id: str) -> dict:
    """Aggregate every signal feed into one consolidated, per-source-status overview."""
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company first.")
    sections = [
        _events_section(session, workspace_id),
        _insiders_section(session, workspace_id),
        _themes_section(session, workspace_id),
        _news_section(target.name),
    ]
    return {
        "workspace_id": workspace_id,
        "sections": sections,
        "overall_status": _overall_status([s["source_status"] for s in sections]),
        "generated_at": now_utc(),
    }
