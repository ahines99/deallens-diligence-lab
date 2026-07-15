"""SEC EDGAR client — real public-company data.

Fetches ticker->CIK mapping, submissions (filing metadata), XBRL company facts (financials),
and primary-document text (for risk-factor / MD&A extraction). No API key required; SEC's
fair-access policy only requires a descriptive User-Agent.

All network access is centralized here so services stay testable.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import httpx

from src.config import settings

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

_MAX_DOC_BYTES = 8_000_000

# Optional on-disk response cache (apps/api/data/cache, gitignored). Off by default so
# research always sees live EDGAR; demo deployments set EDGAR_CACHE_TTL_SECONDS so repeat
# visitors don't re-download the same filings and the SEC fair-access budget is respected.
_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


class EdgarError(Exception):
    """Raised when EDGAR is unreachable or returns unexpected data."""


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, headers=_headers(), follow_redirects=True)


def _cache_read(url: str, kind: str) -> str | None:
    ttl = settings.edgar_cache_ttl_seconds
    if ttl <= 0:
        return None
    path = _CACHE_DIR / f"{kind}-{hashlib.sha256(url.encode('utf-8')).hexdigest()}.cache"
    try:
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def _cache_write(url: str, kind: str, payload: str) -> None:
    if settings.edgar_cache_ttl_seconds <= 0:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / f"{kind}-{hashlib.sha256(url.encode('utf-8')).hexdigest()}.cache"
        path.write_text(payload, encoding="utf-8")
    except OSError:
        # Caching is best-effort; a full disk must never break live research.
        pass


def _get_json(url: str) -> dict:
    cached = _cache_read(url, "json")
    if cached is not None:
        return json.loads(cached)
    try:
        with _client() as c:
            resp = c.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise EdgarError(f"EDGAR request failed for {url}: {exc}") from exc
    _cache_write(url, "json", json.dumps(payload))
    return payload


# --- Ticker -> CIK --------------------------------------------------------
@lru_cache(maxsize=1)
def _ticker_map() -> dict[str, dict]:
    data = _get_json(SEC_TICKERS_URL)
    out: dict[str, dict] = {}
    for row in data.values():
        out[row["ticker"].upper()] = {
            "cik": str(row["cik_str"]).zfill(10),
            "ticker": row["ticker"].upper(),
            "name": row["title"],
        }
    return out


def resolve_ticker(ticker: str) -> dict:
    t = (ticker or "").strip().upper()
    m = _ticker_map()
    if t not in m:
        raise EdgarError(f"Ticker '{ticker}' not found in SEC company list.")
    return m[t]


def search_companies(query: str, limit: int = 15) -> list[dict]:
    q = (query or "").strip().upper()
    m = _ticker_map()
    if not q:
        # Return a stable handful of well-known names when no query is given.
        return list(m.values())[:limit]
    exact = [v for k, v in m.items() if k == q]
    starts = [v for k, v in m.items() if k.startswith(q) and k != q]
    name_hits = [v for v in m.values() if q.lower() in v["name"].lower()]
    seen: set[str] = set()
    out: list[dict] = []
    for v in exact + starts + name_hits:
        if v["ticker"] in seen:
            continue
        seen.add(v["ticker"])
        out.append(v)
        if len(out) >= limit:
            break
    return out


# --- Submissions (filing metadata) ----------------------------------------
@dataclass
class FilingMeta:
    form: str
    filing_date: str
    accession: str
    primary_document: str
    primary_doc_url: str
    report_date: str


def get_submissions(cik10: str) -> dict:
    return _get_json(SUBMISSIONS_URL.format(cik10=cik10))


def recent_filings(cik10: str, forms: tuple[str, ...], limit: int) -> list[FilingMeta]:
    data = get_submissions(cik10)
    recent = data.get("filings", {}).get("recent", {})
    f = recent.get("form", [])
    dates = recent.get("filingDate", [])
    reports = recent.get("reportDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cik_int = int(cik10)
    out: list[FilingMeta] = []
    for i, form in enumerate(f):
        if form not in forms:
            continue
        acc = accs[i] if i < len(accs) else ""
        acc_nodash = acc.replace("-", "")
        doc = docs[i] if i < len(docs) else ""
        url = ARCHIVES.format(cik=cik_int, acc=acc_nodash, doc=doc) if acc_nodash and doc else ""
        out.append(
            FilingMeta(
                form=form,
                filing_date=dates[i] if i < len(dates) else "",
                accession=acc,
                primary_document=doc,
                primary_doc_url=url,
                report_date=reports[i] if i < len(reports) else "",
            )
        )
        if len(out) >= limit:
            break
    return out


def company_name(cik10: str) -> str:
    return get_submissions(cik10).get("name", "")


# --- XBRL company facts ---------------------------------------------------
def get_company_facts(cik10: str) -> dict:
    return _get_json(COMPANY_FACTS_URL.format(cik10=cik10))


_ANNUAL_DURATION = re.compile(r"^CY\d{4}$")
_INSTANT_FRAME = re.compile(r"^CY\d{4}Q[1-4]I$")

# A discrete fiscal quarter is 13 weeks (91 days) or 14 weeks (98 days) for 52/53-week issuers;
# the bounds absorb month-end drift without admitting half-year or annual duration facts.
QUARTER_MIN_DAYS = 75
QUARTER_MAX_DAYS = 115


def _duration_days(start: str, end: str) -> int | None:
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def quarterly_points(facts: dict, concept: str, unit: str = "USD") -> list[dict]:
    """Return the latest filed value for each discrete quarterly duration period, oldest first.

    A quarterly point is any duration fact whose span is one fiscal quarter (see the day bounds
    above) — this catches 10-Q facts (fp Q1/Q2/Q3) as well as frame-less comparative quarters and
    the rare discretely tagged Q4. Company Facts retains comparative values from later filings, so
    keeping the most recently filed point per (start, end) period preserves amendments and
    restatements without double-counting, mirroring ``annual_points``.
    """
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    series = node.get("units", {}).get(unit, [])
    by_period: dict[tuple[str, str], dict] = {}
    for point in series:
        start = point.get("start", "")
        end = point.get("end", "")
        if not start or not end:
            continue
        days = _duration_days(start, end)
        if days is None or not (QUARTER_MIN_DAYS <= days <= QUARTER_MAX_DAYS):
            continue
        key = (start, end)
        existing = by_period.get(key)
        ordering = (point.get("filed", ""), point.get("accn", ""))
        existing_ordering = (
            (existing or {}).get("filed", ""),
            (existing or {}).get("accn", ""),
        )
        if existing is None or ordering >= existing_ordering:
            by_period[key] = point
    return [by_period[key] for key in sorted(by_period, key=lambda k: (k[1], k[0]))]


def annual_points(facts: dict, concept: str, instant: bool = False, unit: str = "USD") -> list[dict]:
    """Return the latest filed value for each annual reporting period, oldest first.

    `unit` defaults to "USD"; pass "shares" for share-count concepts (which live under units.shares).
    Duration facts are keyed by the issuer fiscal year when SEC supplies ``fy`` (falling back to
    the annual frame). Instant facts are keyed by balance-sheet
    date and, when filing context is present, must come from a fiscal-year 10-K. This intentionally
    accepts Q1/Q2/Q3 instant frames for non-December fiscal year ends instead of assuming Q4.
    Company Facts retains comparative values from later filings, so keeping the most recently filed
    point preserves amendments/restatements without double-counting a period.
    """
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    series = node.get("units", {}).get(unit, [])
    by_period: dict[str, dict] = {}
    for point in series:
        frame = point.get("frame", "")
        if instant:
            form = point.get("form", "")
            fp = point.get("fp", "")
            has_filing_context = bool(form or fp)
            is_fiscal_annual = form in {"10-K", "10-K/A"} and fp == "FY"
            if has_filing_context and not is_fiscal_annual:
                continue
            if not is_fiscal_annual and not _INSTANT_FRAME.match(frame):
                continue
            period_key = point.get("end", "") or frame
        else:
            if not _ANNUAL_DURATION.match(frame):
                continue
            fiscal_year = str(point.get("fy") or "").strip()
            period_key = f"FY{fiscal_year}" if fiscal_year else frame
        if not period_key:
            continue
        existing = by_period.get(period_key)
        ordering = (point.get("filed", ""), point.get("accn", ""))
        existing_ordering = (
            (existing or {}).get("filed", ""),
            (existing or {}).get("accn", ""),
        )
        if existing is None or ordering >= existing_ordering:
            by_period[period_key] = point
    return [by_period[period] for period in sorted(by_period)]


def pick_concept(
    facts: dict, concepts: list[str], instant: bool = False, unit: str = "USD"
) -> tuple[str | None, list[dict]]:
    """Return (concept, annual_points) for the first concept that has annual data."""
    for c in concepts:
        pts = annual_points(facts, c, instant=instant, unit=unit)
        if pts:
            return c, pts
    return None, []


# --- Primary document text ------------------------------------------------
def fetch_document_text(url: str) -> str:
    if not url:
        return ""
    cached_text = _cache_read(url, "doc")
    if cached_text is not None:
        return cached_text
    try:
        with _client() as c:
            resp = c.get(url)
            resp.raise_for_status()
            raw = resp.content[:_MAX_DOC_BYTES]
    except httpx.HTTPError as exc:
        raise EdgarError(f"Failed to fetch filing document {url}: {exc}") from exc

    from bs4 import BeautifulSoup

    # Pass raw bytes so BeautifulSoup detects the declared charset (SEC uses smart quotes/dashes).
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ")
    # Normalize non-breaking spaces and collapse whitespace.
    text = text.replace("\xa0", " ").replace("​", "")
    text = re.sub(r"\s+", " ", text).strip()
    _cache_write(url, "doc", text)
    return text


def fetch_document_html(url: str) -> str:
    """Fetch a filing's primary document as decoded HTML (tags preserved).

    Unlike ``fetch_document_text`` (which strips markup for prose extraction), this keeps the
    HTML intact so table-structured disclosures — notably the DEF 14A Summary Compensation
    Table — can be parsed by row/column. Same byte cap and on-disk cache discipline.
    """
    if not url:
        return ""
    cached = _cache_read(url, "html")
    if cached is not None:
        return cached
    try:
        with _client() as c:
            resp = c.get(url)
            resp.raise_for_status()
            raw = resp.content[:_MAX_DOC_BYTES]
    except httpx.HTTPError as exc:
        raise EdgarError(f"Failed to fetch filing document {url}: {exc}") from exc

    from bs4 import BeautifulSoup

    # Parse raw bytes so BeautifulSoup honors the declared charset, then re-serialize to a
    # normalized HTML string suitable for caching and downstream table parsing.
    html = str(BeautifulSoup(raw, "html.parser"))
    _cache_write(url, "html", html)
    return html


# A courtesy throttle so we stay well under SEC's 10 req/s guidance.
def polite_pause(seconds: float = 0.2) -> None:
    time.sleep(seconds)
