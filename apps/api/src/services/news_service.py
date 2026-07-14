"""News signals via GDELT DOC 2.0 (keyless, no API key required).

Pulls recent English-language news articles that mention the target company. This is UNVERIFIED
MEDIA — it is explicitly NOT part of the evidence table and must never be cited as diligence
evidence. GDELT is best-effort: it occasionally returns non-JSON, HTML error pages, or empty
bodies, so failure paths return an explicit unavailable status rather than a false clean empty.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("deallens.news")

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 15
_TIMEOUT = 20
# GDELT does not require a User-Agent, but a descriptive one is polite.
_USER_AGENT = "DealLens Diligence Lab (portfolio project) contact@example.com"

# Corporate suffixes trimmed so the query matches the recognizable brand, longest first.
_SUFFIXES = (
    " HOLDING CORPORATION", " HOLDINGS CORPORATION", " HOLDING CORP", " HOLDINGS CORP",
    " HOLDINGS INC", " HOLDINGS INC.", " HOLDINGS", " HOLDING", " CORPORATION", " CORP.",
    " CORP", ", INC.", ", INC", " INC.", " INC", " CO.", " COMPANY", " PLC", " N.V.",
    " L.P.", " LP", " L.L.C.", " LLC", " LTD.", " LTD",
)


def _clean_company(name: str) -> str:
    """Trim a trailing corporate suffix so the news query matches the brand, not the legal entity."""
    original = (name or "").strip()
    n = original.upper()
    for suffix in _SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
            break
    return n.title() if n else original


def build_query(company: str) -> str:
    """GDELT query string: phrase-match the (cleaned) company name, English sources only."""
    clean = _clean_company(company)
    # Quote multi-word names so GDELT phrase-matches instead of OR-ing the tokens.
    phrase = f'"{clean}"' if " " in clean else clean
    return f"{phrase} sourcelang:english"


def _parse_articles(data: object) -> list[dict]:
    """Extract well-formed article rows from a GDELT DOC response (defensive)."""
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for a in data.get("articles", []) or []:
        if not isinstance(a, dict):
            continue
        url = (a.get("url") or "").strip()
        title = (a.get("title") or "").strip()
        if not url or not title:
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "domain": (a.get("domain") or "").strip(),
                "seendate": (a.get("seendate") or "").strip(),
                "sourcecountry": (a.get("sourcecountry") or "").strip() or None,
            }
        )
    return out


def fetch_news(company: str, max_records: int = MAX_RECORDS) -> dict:
    """Return articles plus an explicit source-availability state."""
    query = build_query(company)
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "sort": "datedesc",
    }
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as c:
            resp = c.get(GDELT_DOC, params=params)
            resp.raise_for_status()
            # GDELT returns HTML/plain-text error notices (e.g. "query too short") with a 200,
            # and sometimes an empty body — resp.json() raises ValueError, which we swallow.
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GDELT news fetch failed for %r: %s", query, exc)
        return {
            "query": query,
            "articles": [],
            "source_status": "unavailable",
            "source_error": "GDELT could not be reached or returned an invalid response.",
        }

    if not isinstance(data, dict):
        return {
            "query": query,
            "articles": [],
            "source_status": "unavailable",
            "source_error": "GDELT returned an invalid response shape.",
        }
    return {
        "query": query,
        "articles": _parse_articles(data),
        "source_status": "available",
        "source_error": None,
    }
