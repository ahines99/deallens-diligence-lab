"""Institutional ownership (13F) + activist-stake (13D/13G) signals — real, keyless SEC data.

Two capabilities, both reading live SEC EDGAR (submissions + filing documents) with the descriptive
User-Agent SEC's fair-access policy requires (`settings.sec_user_agent`). No API key.

G14 — institutional_ownership(): 13F holder-concentration analysis.
    The *true* institutional-ownership question — "which 13F managers hold company X?" — is a reverse
    index over every manager's holdings table. That index is not retrievable per-request from keyless
    EDGAR (there is no public endpoint keyed by the held security's CUSIP). We therefore implement the
    honest, tractable case: when the *target itself* files 13F-HR (i.e. it is an institutional manager,
    e.g. Berkshire Hathaway), we fetch its latest information table, parse the holdings, and compute
    portfolio concentration (HHI, top-5 share, holder/position count, total value). When the target is
    an ordinary operating company that files no 13F, we say so explicitly (scope=not_applicable,
    source_status=unavailable) rather than emitting a false-clean empty. The parser + concentration
    math are pure functions, unit-tested on a synthetic information table.

G15 — activist_stakes(): 13D/13G activist-stake detection.
    SC 13D (activist / control intent) and SC 13G (passive) beneficial-ownership filings about the
    target are indexed under the target's CIK in its EDGAR submissions feed. We classify each as
    activist (13D) vs passive (13G), best-effort extract the reporting person and percent owned from
    the filing cover page, and shape them as timeline events that join the signals timeline.

Everything degrades gracefully: a network hiccup or a missing field yields empty/`None`, never a crash,
and never a clean-looking zero that hides an upstream failure.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import httpx

from src.config import settings
from src.db.base import now_utc
from src.services import edgar_client
from src.services.common import NotFound
from src.services.edgar_client import EdgarError
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.ownership")

ARCHIVES_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_JSON = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"

_HOLDINGS_RESPONSE_CAP = 25  # largest positions returned in the payload (concentration uses ALL)
_MAX_STAKE_FETCH = 10  # cap live cover-page fetches for filer/percent extraction, to stay polite

# 13F-HR (and its amendment/notice/combination variants) are the institutional-manager holdings forms.
_THIRTEEN_F_FORMS = {"13F-HR", "13F-HR/A", "13F-CR", "13F-CR/A", "13F-NT", "13F-NT/A"}
# Beneficial-ownership forms filed about a subject company. 13D = activist/control intent; 13G = passive.
# EDGAR relabeled these "SCHEDULE 13D"/"SCHEDULE 13G" in late 2024; both spellings appear in
# submissions feeds, and missing the new ones reads as a false-clean "no activist stakes".
_STAKE_FORMS = {
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
}
_STAKE_EVENT_LIMIT = 40


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _target_with_cik(session, workspace_id: str):
    """Return the target, or raise NotFound if it has no CIK to work from."""
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    if not target.cik:
        raise NotFound("Target has no SEC CIK; ownership feeds require a public (EDGAR) company.")
    return target


def _cik10(cik: str) -> str:
    return str(cik).lstrip("0").zfill(10) if cik else ""


def _archive_url(cik10: str, accession: str, doc: str) -> str | None:
    if not accession or not doc:
        return None
    return ARCHIVES_DIR.format(cik=int(cik10), acc=accession.replace("-", ""), doc=doc)


# ===========================================================================
# G14 — 13F information-table parsing + holder-concentration math (pure)
# ===========================================================================
def _local(tag: str) -> str:
    """Strip an XML namespace from a tag, leaving the local element name."""
    return tag.split("}")[-1]


def _first_local(parent: ET.Element, name: str) -> ET.Element | None:
    for el in parent.iter():
        if _local(el.tag) == name:
            return el
    return None


def _text_local(parent: ET.Element, name: str) -> str:
    el = _first_local(parent, name)
    return (el.text or "").strip() if el is not None else ""


def _num_local(parent: ET.Element, name: str) -> float | None:
    txt = _text_local(parent, name)
    if not txt:
        return None
    try:
        return float(txt.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def parse_13f_infotable(xml_bytes: bytes) -> list[dict]:
    """Parse a 13F-HR information table XML into holding rows. Namespace-agnostic and defensive.

    Each ``<infoTable>`` entry yields {issuer, cusip, title, value, shares}. The reported ``<value>``
    is in whole USD (issuers post-2023Q3) or USD thousands (earlier) — an ambiguity that does NOT
    affect the concentration ratios below, which are scale-invariant. Malformed XML yields [].
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    rows: list[dict] = []
    for it in root.iter():
        if _local(it.tag) != "infoTable":
            continue
        issuer = _text_local(it, "nameOfIssuer")
        rows.append(
            {
                "issuer": issuer or "Unknown",
                "cusip": _text_local(it, "cusip") or None,
                "title": _text_local(it, "titleOfClass") or None,
                "value": _num_local(it, "value"),
                "shares": _num_local(it, "sshPrnamt"),
            }
        )
    return rows


def concentration(holdings: list[dict]) -> dict:
    """Holder/position concentration over a set of value-weighted holdings.

    Returns:
      - ``hhi``        — Herfindahl-Hirschman index = Σ wᵢ² over fractional value weights, in [0, 1]
                         (1.0 = a single position; → 0 = perfectly fragmented). Scale-invariant.
      - ``top5_share`` — combined fractional weight of the five largest positions, in [0, 1].
      - ``holder_count`` — number of positive-value positions.
      - ``total_value``  — summed reported value (unit per ``parse_13f_infotable``).

    Positions with a missing or non-positive value are excluded from the weights (never imputed).
    With no positive-value positions, ``hhi``/``top5_share``/``total_value`` are None.
    """
    values = [h["value"] for h in holdings if h.get("value") is not None and h["value"] > 0]
    holder_count = len(values)
    total = sum(values)
    if total <= 0:
        return {"hhi": None, "top5_share": None, "holder_count": holder_count, "total_value": None}
    weights = sorted((v / total for v in values), reverse=True)
    hhi = sum(w * w for w in weights)
    top5 = sum(weights[:5])
    return {
        "hhi": round(hhi, 6),
        "top5_share": round(top5, 6),
        "holder_count": holder_count,
        "total_value": round(total, 2),
    }


def _empty_concentration() -> dict:
    return {"hhi": None, "top5_share": None, "holder_count": 0, "total_value": None}


def _find_infotable_doc(cik10: str, accession: str) -> bytes | None:
    """Locate and fetch the information-table XML within a 13F-HR accession folder.

    The submissions ``primaryDocument`` for a 13F is the cover page (``primary_doc.xml``); the holdings
    table is a separate XML in the same accession. We read the accession ``index.json`` and fetch the
    first ``.xml`` (other than the cover) whose root is an ``informationTable``. Best-effort; None on
    any failure.
    """
    acc_nodash = accession.replace("-", "")
    idx_url = INDEX_JSON.format(cik=int(cik10), acc=acc_nodash)
    try:
        with httpx.Client(timeout=30, headers=_headers(), follow_redirects=True) as c:
            resp = c.get(idx_url)
            resp.raise_for_status()
            items = (resp.json().get("directory", {}) or {}).get("item", []) or []
        edgar_client.polite_pause()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("13F: index.json fetch failed for %s: %s", idx_url, exc)
        return None

    names = [it.get("name", "") for it in items if it.get("name", "").lower().endswith(".xml")]
    # Try the likeliest holdings-table names first, then any remaining XML (excluding the cover).
    def _rank(n: str) -> int:
        low = n.lower()
        if "primary_doc" in low:
            return 3
        if any(tok in low for tok in ("infotable", "info_table", "form13f", "table")):
            return 0
        return 1

    for name in sorted(names, key=_rank):
        doc_url = _archive_url(cik10, accession, name)
        if not doc_url:
            continue
        try:
            with httpx.Client(timeout=30, headers=_headers(), follow_redirects=True) as c:
                resp = c.get(doc_url)
                resp.raise_for_status()
                raw = resp.content
            edgar_client.polite_pause()
        except httpx.HTTPError as exc:
            logger.warning("13F: candidate fetch failed %s: %s", doc_url, exc)
            continue
        try:
            if _local(ET.fromstring(raw).tag) == "informationTable":
                return raw
        except ET.ParseError:
            continue
    return None


def institutional_ownership(session, workspace_id: str) -> dict:
    """13F institutional ownership + holder-concentration for the target.

    Honest scoping: if the target itself files 13F-HR (it is a manager), report its holdings and their
    concentration. Otherwise report scope=not_applicable — keyless reverse holder-lookup is unavailable.
    """
    target = _target_with_cik(session, workspace_id)
    cik10 = _cik10(target.cik)
    try:
        sub = edgar_client.get_submissions(cik10)
    except EdgarError as exc:
        logger.warning("institutional_ownership: submissions fetch failed for %s: %s", cik10, exc)
        return {
            "workspace_id": workspace_id,
            "scope": "not_applicable",
            "manager_name": None,
            "period_of_report": None,
            "holdings": [],
            "concentration": _empty_concentration(),
            "source_status": "unavailable",
            "source_error": "SEC EDGAR submissions are temporarily unavailable.",
            "note": "Institutional ownership could not be retrieved; SEC EDGAR is unavailable.",
            "generated_at": now_utc(),
        }

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    reports = recent.get("reportDate", [])
    accs = recent.get("accessionNumber", [])

    idx = next((i for i, f in enumerate(forms) if f in _THIRTEEN_F_FORMS), None)
    if idx is None:
        return {
            "workspace_id": workspace_id,
            "scope": "not_applicable",
            "manager_name": sub.get("name") or target.name,
            "period_of_report": None,
            "holdings": [],
            "concentration": _empty_concentration(),
            "source_status": "unavailable",
            "source_error": None,
            "note": (
                f"{target.name} files no Form 13F-HR, so it is not an institutional manager whose "
                "holdings we can report. The inverse question — which 13F managers hold this company — "
                "requires a full reverse index over every manager's holdings table (keyed by the held "
                "security's CUSIP), which is not retrievable from keyless SEC EDGAR per request."
            ),
            "generated_at": now_utc(),
        }

    accession = accs[idx] if idx < len(accs) else ""
    raw = _find_infotable_doc(cik10, accession) if accession else None
    if raw is None:
        return {
            "workspace_id": workspace_id,
            "scope": "manager_portfolio",
            "manager_name": sub.get("name") or target.name,
            "period_of_report": reports[idx] if idx < len(reports) else None,
            "holdings": [],
            "concentration": _empty_concentration(),
            "source_status": "unavailable",
            "source_error": "The 13F information table could not be retrieved from SEC EDGAR.",
            "note": (
                f"{target.name} files Form 13F-HR ({forms[idx]}), but its latest information table "
                "could not be fetched from EDGAR."
            ),
            "generated_at": now_utc(),
        }

    holdings = parse_13f_infotable(raw)
    conc = concentration(holdings)
    ranked = sorted(
        holdings, key=lambda h: (h.get("value") is not None, h.get("value") or 0), reverse=True
    )
    return {
        "workspace_id": workspace_id,
        "scope": "manager_portfolio",
        "manager_name": sub.get("name") or target.name,
        "period_of_report": reports[idx] if idx < len(reports) else None,
        "holdings": ranked[:_HOLDINGS_RESPONSE_CAP],
        "concentration": conc,
        "source_status": "available",
        "source_error": None,
        "note": (
            f"{target.name} is itself a Form 13F filer; this reports the concentration of ITS reported "
            f"holdings ({conc['holder_count']} positions), not who holds {target.name}. Values are as "
            "reported on Form 13F (USD thousands pre-2023Q3, USD thereafter); concentration ratios are "
            f"scale-invariant. Filing {forms[idx]} for period {reports[idx] if idx < len(reports) else 'n/a'}."
        ),
        "generated_at": now_utc(),
    }


# ===========================================================================
# G15 — 13D/13G activist-stake classification (pure) + live enrichment
# ===========================================================================
def classify_stake(form: str) -> dict:
    """Classify a beneficial-ownership form. SC 13D => activist (control intent); SC 13G => passive.

    Returns {type: '13D'|'13G', is_activist, is_amendment}. A trailing '/A' marks an amendment.
    Raises ValueError for a form that is not a 13D/13G variant.
    """
    f = (form or "").strip().upper()
    is_amendment = f.endswith("/A")
    base = f[:-2] if is_amendment else f
    base = base.replace("SCHEDULE ", "SC ").strip()
    if base in ("SC 13D", "13D"):
        return {"type": "13D", "is_activist": True, "is_amendment": is_amendment}
    if base in ("SC 13G", "13G"):
        return {"type": "13G", "is_activist": False, "is_amendment": is_amendment}
    raise ValueError(f"Not a 13D/13G beneficial-ownership form: {form!r}")


def build_stake_event(meta: dict) -> dict:
    """Shape a classified 13D/13G filing into a signals-timeline event.

    ``meta`` carries at least {form, filing_date}; optionally {filer, accession, url, percent_owned}.
    Missing filer/percent are preserved as None (never imputed).
    """
    cls = classify_stake(meta.get("form", ""))
    return {
        "type": cls["type"],
        "form": meta.get("form", ""),
        "filer": meta.get("filer") or None,
        "filing_date": meta.get("filing_date", ""),
        "accession": meta.get("accession") or None,
        "url": meta.get("url") or None,
        "percent_owned": meta.get("percent_owned"),
        "is_activist": cls["is_activist"],
        "is_amendment": cls["is_amendment"],
    }


_PERSON_RE = re.compile(
    r"NAME[S]?\s+OF\s+REPORTING\s+PERSON[S]?.{0,80}?[\r\n> ]\s*([A-Z0-9][^\n<]{2,80}?)\s*(?:"
    r"S\.?S\.?\s*OR\s*I\.?R\.?S\.?|\(2\)|CHECK\s+THE\s+APPROPRIATE)",
    re.IGNORECASE | re.DOTALL,
)
_PERCENT_RE = re.compile(
    r"PERCENT\s+OF\s+CLASS\s+REPRESENTED\s+BY\s+AMOUNT\s+IN\s+ROW.{0,120}?(\d{1,3}(?:\.\d+)?)\s*%",
    re.IGNORECASE | re.DOTALL,
)


def extract_reporting_person(text: str) -> str | None:
    """Best-effort filer name from a 13D/13G cover page. None when the pattern is absent."""
    m = _PERSON_RE.search(text or "")
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
    return name or None


def extract_percent(text: str) -> float | None:
    """Best-effort 'percent of class' from a 13D/13G cover page (Row 13). None when absent."""
    m = _PERCENT_RE.search(text or "")
    if not m:
        return None
    try:
        pct = float(m.group(1))
    except ValueError:
        return None
    return pct if 0 <= pct <= 100 else None


def activist_stakes(session, workspace_id: str) -> dict:
    """Recent SC 13D/13G filings about the target, classified and shaped as timeline events.

    Detection is from the target's EDGAR submissions (13D/13G filings are indexed under the subject
    CIK). Filer + percent-owned are best-effort enriched from the filing cover page (capped fetches).
    """
    target = _target_with_cik(session, workspace_id)
    cik10 = _cik10(target.cik)
    try:
        recent = edgar_client.get_submissions(cik10).get("filings", {}).get("recent", {})
    except EdgarError as exc:
        logger.warning("activist_stakes: submissions fetch failed for %s: %s", cik10, exc)
        return {
            "workspace_id": workspace_id,
            "events": [],
            "source_status": "unavailable",
            "source_error": "SEC EDGAR submissions are temporarily unavailable.",
            "note": "Activist-stake filings could not be retrieved; SEC EDGAR is unavailable.",
            "generated_at": now_utc(),
        }

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    events: list[dict] = []
    fetched = 0
    fetch_errors = 0
    for i, form in enumerate(forms):
        if len(events) >= _STAKE_EVENT_LIMIT:
            break
        if form not in _STAKE_FORMS:
            continue
        acc = accs[i] if i < len(accs) else ""
        doc = docs[i] if i < len(docs) else ""
        url = _archive_url(cik10, acc, doc)
        filer = None
        percent = None
        if fetched < _MAX_STAKE_FETCH and url:
            try:
                text = edgar_client.fetch_document_text(url)
                filer = extract_reporting_person(text)
                percent = extract_percent(text)
                fetched += 1
            except EdgarError as exc:
                logger.warning("activist_stakes: cover-page fetch failed %s: %s", url, exc)
                fetch_errors += 1
        events.append(
            build_stake_event(
                {
                    "form": form,
                    "filing_date": dates[i] if i < len(dates) else "",
                    "filer": filer,
                    "accession": acc or None,
                    "url": url,
                    "percent_owned": percent,
                }
            )
        )

    return {
        "workspace_id": workspace_id,
        "events": events,
        "source_status": "partial" if fetch_errors else "available",
        "source_error": (
            f"{fetch_errors} beneficial-ownership filing(s) could not be retrieved for enrichment."
            if fetch_errors
            else None
        ),
        "note": (
            "SC 13D filings signal activist / control intent; SC 13G filings are passive holdings. "
            "Filer and percent-owned are best-effort from the filing cover page and may be absent."
        ),
        "generated_at": now_utc(),
    }
