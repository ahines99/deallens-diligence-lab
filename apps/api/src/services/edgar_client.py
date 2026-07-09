"""SEC EDGAR client — real public-company data.

Fetches ticker->CIK mapping, submissions (filing metadata), XBRL company facts (financials),
and primary-document text (for risk-factor / MD&A extraction). No API key required; SEC's
fair-access policy only requires a descriptive User-Agent.

All network access is centralized here so services stay testable.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx

from src.config import settings

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

_MAX_DOC_BYTES = 8_000_000


class EdgarError(Exception):
    """Raised when EDGAR is unreachable or returns unexpected data."""


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, headers=_headers(), follow_redirects=True)


def _get_json(url: str) -> dict:
    try:
        with _client() as c:
            resp = c.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise EdgarError(f"EDGAR request failed for {url}: {exc}") from exc


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
_ANNUAL_INSTANT = re.compile(r"^CY\d{4}Q4I$")


def annual_points(facts: dict, concept: str, instant: bool = False) -> list[dict]:
    """Return de-duplicated annual XBRL points (via 'frame') for a us-gaap concept, oldest first."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    usd = node.get("units", {}).get("USD", [])
    pat = _ANNUAL_INSTANT if instant else _ANNUAL_DURATION
    pts = [u for u in usd if pat.match(u.get("frame", ""))]
    pts.sort(key=lambda u: u["end"])
    return pts


def pick_concept(facts: dict, concepts: list[str], instant: bool = False) -> tuple[str | None, list[dict]]:
    """Return (concept, annual_points) for the first concept that has annual data."""
    for c in concepts:
        pts = annual_points(facts, c, instant=instant)
        if pts:
            return c, pts
    return None, []


# --- Primary document text ------------------------------------------------
def fetch_document_text(url: str) -> str:
    if not url:
        return ""
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
    return text


# A courtesy throttle so we stay well under SEC's 10 req/s guidance.
def polite_pause(seconds: float = 0.2) -> None:
    time.sleep(seconds)
