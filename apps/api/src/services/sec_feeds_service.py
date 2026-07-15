"""SEC event / insider / theme feeds + SIC auto-peer discovery — all real, keyless SEC data.

Everything here reads live SEC EDGAR (submissions, Form 4 ownership XML, full-text search) using the
descriptive User-Agent SEC's fair-access policy requires (`settings.sec_user_agent`). No API key.

- events():   recent filings decoded into an 8-K item-code timeline, flagging significant events.
- insiders(): recent Form 4 filings parsed into insider buy/sell transactions (last ~90 days).
- themes():   EDGAR full-text search (EFTS) for a fixed red-flag theme set, per-theme hit counts.
- auto_comps(): discover same-SIC public peers and add them via the existing benchmark service.
- risk_flags(): deterministic red flags (significant 8-K events, heavy insider selling) for the
                integration agent to splice into `analysis_service` (same shape as RiskAnalyst flags).

Everything degrades gracefully: a network hiccup or a missing field yields empty/`None`, never a crash.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from src.config import settings
from src.db.base import now_utc
from src.services import edgar_client, financial_benchmark_service as bench
from src.services.common import NotFound
from src.services.edgar_client import EdgarError
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.sec_feeds")

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
BROWSE_EDGAR = "https://www.sec.gov/cgi-bin/browse-edgar"
ARCHIVES_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

INSIDER_WINDOW_DAYS = 90
_MAX_FORM4_FETCH = 12  # cap live Form 4 XML fetches to stay polite
_EVENT_LIMIT = 40  # most-recent filings scanned for the timeline

# 8-K item code -> human label (subset of SEC's Form 8-K item taxonomy).
EIGHT_K_ITEMS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}
# Items that materially move diligence and mark an event as significant.
SIGNIFICANT_ITEMS = {"4.02", "4.01", "1.05", "2.01", "5.02"}

# Fixed red-flag theme set for the full-text theme scan.
THEMES: list[tuple[str, str, str]] = [
    ("going_concern", "Going concern", "going concern"),
    ("material_weakness", "Material weakness", "material weakness"),
    ("restatement", "Restatement", "restatement"),
    ("impairment", "Impairment", "impairment"),
    ("customer_concentration", "Customer concentration", "customer concentration"),
    ("goodwill_impairment", "Goodwill impairment", "goodwill impairment"),
]

_ITEM_CODE = re.compile(r"\b(\d\.\d{2})\b")


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _target_with_cik(session, workspace_id: str):
    """Return the target, or raise NotFound if it has no financials/CIK to work from."""
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    if not target.cik:
        raise NotFound("Target has no SEC CIK; SEC feeds require a public (EDGAR) company.")
    return target


def _cik10(cik: str) -> str:
    return str(cik).lstrip("0").zfill(10) if cik else ""


def _archive_url(cik10: str, accession: str, doc: str) -> str | None:
    if not accession or not doc:
        return None
    return ARCHIVES_DIR.format(cik=int(cik10), acc=accession.replace("-", ""), doc=doc)


def _split_items(raw: str) -> list[str]:
    """Extract distinct '#.##' item codes from a submissions `items` string, order-preserving."""
    seen: list[str] = []
    for code in _ITEM_CODE.findall(raw or ""):
        if code not in seen:
            seen.append(code)
    return seen


# --- Events ----------------------------------------------------------------
def events(session, workspace_id: str) -> dict:
    target = _target_with_cik(session, workspace_id)
    cik10 = _cik10(target.cik)
    try:
        recent = edgar_client.get_submissions(cik10).get("filings", {}).get("recent", {})
    except EdgarError as exc:
        logger.warning("events: submissions fetch failed for %s: %s", cik10, exc)
        return {
            "workspace_id": workspace_id,
            "events": [],
            "source_status": "unavailable",
            "source_error": "SEC EDGAR submissions are temporarily unavailable.",
            "generated_at": now_utc(),
        }

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    rows: list[dict] = []
    for i, form in enumerate(forms):
        if len(rows) >= _EVENT_LIMIT:
            break
        raw_items = items[i] if i < len(items) else ""
        codes = _split_items(raw_items) if form.startswith("8-K") else []
        # Keep 8-Ks (with decoded items) and periodic reports; skip routine ownership/other noise.
        if not (form.startswith("8-K") or form.startswith("10-K") or form.startswith("10-Q")):
            continue
        acc = accs[i] if i < len(accs) else ""
        doc = docs[i] if i < len(docs) else ""
        significant = any(c in SIGNIFICANT_ITEMS for c in codes)
        rows.append(
            {
                "date": dates[i] if i < len(dates) else "",
                "form": form,
                "items": [{"code": c, "label": EIGHT_K_ITEMS.get(c, "Other reported item")} for c in codes],
                "accession": acc or None,
                "url": _archive_url(cik10, acc, doc),
                "significant": significant,
            }
        )
    return {
        "workspace_id": workspace_id,
        "events": rows,
        "source_status": "available",
        "source_error": None,
        "generated_at": now_utc(),
    }


# --- Insiders (Form 4) -----------------------------------------------------
def _text(node: ET.Element | None) -> str:
    """Return stripped text of a node's <value> child, or the node's own text."""
    if node is None:
        return ""
    val = node.find("value")
    if val is not None and val.text:
        return val.text.strip()
    return (node.text or "").strip()


def _num(node: ET.Element | None) -> float | None:
    t = _text(node)
    if not t:
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


_RULE_10B5_RE = re.compile(r"10b5[\s\-]?1", re.IGNORECASE)


def _footnote_map(root: ET.Element) -> dict[str, str]:
    """Map footnote id -> text so per-transaction Rule 10b5-1 references can be resolved."""
    out: dict[str, str] = {}
    for fn in root.findall(".//footnotes/footnote"):
        fid = fn.get("id")
        if fid:
            out[fid] = (fn.text or "").strip()
    return out


def _doc_10b5_1_flag(root: ET.Element) -> bool | None:
    """Document-level Rule 10b5-1 checkbox (added by the 2023 Form 4 amendments).

    Returns True/False when the checkbox element is present, or None when the filing predates
    the checkbox or omits it — an absent flag is 'unknown', never assumed discretionary.
    """
    for el in root.iter():
        if "10b5" in el.tag.split("}")[-1].lower():
            val = _text(el).strip().lower()
            if val in ("1", "true", "yes"):
                return True
            if val in ("0", "false", "no"):
                return False
    return None


def _tx_10b5_1_flag(tx: ET.Element, footnotes: dict[str, str], doc_plan: bool | None) -> bool | None:
    """Per-transaction Rule 10b5-1 status: a referenced footnote naming the rule wins; otherwise
    fall back to the document-level checkbox (itself possibly None/unknown)."""
    for ref in tx.findall(".//footnoteId"):
        if _RULE_10B5_RE.search(footnotes.get(ref.get("id", ""), "")):
            return True
    return doc_plan


def _form4_xml_url(cik10: str, accession: str, primary_doc: str) -> str | None:
    """Best-effort raw-XML URL for a Form 4 (strip any XSL-render wrapper directory)."""
    if not accession:
        return None
    doc = (primary_doc or "").split("/")[-1]  # drop 'xslF345X0N/' render prefix if present
    if not doc:
        return None
    if not doc.lower().endswith(".xml"):
        # primaryDocument is the rendered HTML; the raw ownership XML sits beside it.
        doc = doc.rsplit(".", 1)[0] + ".xml"
    return _archive_url(cik10, accession, doc)


def _parse_form4(xml_bytes: bytes, display_url: str | None) -> list[dict]:
    """Parse a Form 4 ownership document into transaction rows. Defensive against schema drift."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    owner = root.find(".//reportingOwner")
    name = ""
    role = ""
    is_officer = is_director = is_ten_percent_owner = False
    if owner is not None:
        name = _text(owner.find(".//rptOwnerName")) or _text(owner.find("reportingOwnerId/rptOwnerName"))
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            title = _text(rel.find("officerTitle"))
            is_director = _text(rel.find("isDirector")) in ("1", "true")
            is_ten_percent_owner = _text(rel.find("isTenPercentOwner")) in ("1", "true")
            # officerTitle is only populated for officers, so treat its presence as the officer flag.
            is_officer = _text(rel.find("isOfficer")) in ("1", "true") or bool(title)
            if title:
                role = title
            else:
                roles = []
                if is_director:
                    roles.append("Director")
                if is_ten_percent_owner:
                    roles.append("10% Owner")
                if is_officer:
                    roles.append("Officer")
                role = ", ".join(roles)

    footnotes = _footnote_map(root)
    doc_plan = _doc_10b5_1_flag(root)

    rows: list[dict] = []
    # Non-derivative + derivative transactions both carry the A/D coding we care about.
    for tx in root.findall(".//nonDerivativeTransaction") + root.findall(".//derivativeTransaction"):
        amounts = tx.find("transactionAmounts")
        if amounts is None:
            continue
        shares = _num(amounts.find("transactionShares"))
        price = _num(amounts.find("transactionPricePerShare"))
        ad = _text(amounts.find("transactionAcquiredDisposedCode")).upper()
        transaction_code = _text(tx.find("transactionCoding/transactionCode")).upper()
        # A/D only describes whether securities were acquired or disposed. It does not distinguish
        # an open-market trade from grants, option exercises, gifts, or tax withholding. SEC Form 4
        # transaction codes P and S are the open-market/private purchase and sale codes.
        tx_type = (
            "buy"
            if transaction_code == "P"
            else "sell"
            if transaction_code == "S"
            else "other"
        )
        date = _text(tx.find("transactionDate"))
        value = round(shares * price, 2) if (shares is not None and price is not None) else None
        rows.append(
            {
                "date": date,
                "insider": name or "Unknown",
                "role": role,
                "type": tx_type,
                "transaction_code": transaction_code or None,
                "acquired_disposed_code": ad or None,
                "shares": shares,
                "price": price,
                "value": value,
                "plan_10b5_1": _tx_10b5_1_flag(tx, footnotes, doc_plan),
                "is_officer": is_officer,
                "is_director": is_director,
                "is_ten_percent_owner": is_ten_percent_owner,
                "url": display_url,
            }
        )
    return rows


def insiders(session, workspace_id: str) -> dict:
    target = _target_with_cik(session, workspace_id)
    cik10 = _cik10(target.cik)
    try:
        recent = edgar_client.get_submissions(cik10).get("filings", {}).get("recent", {})
    except EdgarError as exc:
        logger.warning("insiders: submissions fetch failed for %s: %s", cik10, exc)
        return {
            "workspace_id": workspace_id,
            "summary": {
                "buys": None,
                "sells": None,
                "net_shares": None,
                "window_days": INSIDER_WINDOW_DAYS,
            },
            "transactions": [],
            "source_status": "unavailable",
            "source_error": "SEC EDGAR insider filings are temporarily unavailable.",
            "generated_at": now_utc(),
        }

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=INSIDER_WINDOW_DAYS)).isoformat()
    transactions: list[dict] = []
    fetched = 0
    fetch_errors = 0
    for i, form in enumerate(forms):
        if fetched >= _MAX_FORM4_FETCH:
            break
        if form != "4":
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if filing_date and filing_date < cutoff:
            break  # recent[] is newest-first; nothing older is in-window
        acc = accs[i] if i < len(accs) else ""
        doc = docs[i] if i < len(docs) else ""
        xml_url = _form4_xml_url(cik10, acc, doc)
        display_url = _archive_url(cik10, acc, doc)
        if not xml_url:
            continue
        try:
            with httpx.Client(timeout=30, headers=_headers(), follow_redirects=True) as c:
                resp = c.get(xml_url)
                resp.raise_for_status()
                raw = resp.content
            edgar_client.polite_pause()
        except httpx.HTTPError as exc:
            logger.warning("insiders: Form 4 fetch failed %s: %s", xml_url, exc)
            fetch_errors += 1
            continue
        fetched += 1
        transactions.extend(_parse_form4(raw, display_url))

    buys = sum(1 for t in transactions if t["type"] == "buy")
    sells = sum(1 for t in transactions if t["type"] == "sell")
    net = None
    share_rows = [t["shares"] for t in transactions if t["shares"] is not None and t["type"] in ("buy", "sell")]
    if share_rows:
        net = sum(
            (t["shares"] if t["type"] == "buy" else -t["shares"])
            for t in transactions
            if t["shares"] is not None and t["type"] in ("buy", "sell")
        )
    return {
        "workspace_id": workspace_id,
        "summary": {"buys": buys, "sells": sells, "net_shares": net, "window_days": INSIDER_WINDOW_DAYS},
        "transactions": transactions,
        "source_status": "partial" if fetch_errors else "available",
        "source_error": (
            f"{fetch_errors} Form 4 filing(s) could not be retrieved."
            if fetch_errors
            else None
        ),
        "generated_at": now_utc(),
    }


# --- Insider-pattern analytics (clusters / 10b5-1 plans / role split) -------
CLUSTER_GAP_DAYS = 7  # max gap (days) between adjacent same-direction trades in one window


def _tx_date(row: dict):
    """Parse a transaction's ISO date to a date, or None if absent/unparseable."""
    try:
        return datetime.strptime((row.get("date") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _make_cluster(rows: list[dict], direction: str) -> dict:
    dates = sorted(d for d in (_tx_date(r) for r in rows) if d is not None)
    share_rows = [r["shares"] for r in rows if r.get("shares") is not None]
    value_rows = [r["value"] for r in rows if r.get("value") is not None]
    return {
        "direction": direction,
        "start": dates[0].isoformat() if dates else "",
        "end": dates[-1].isoformat() if dates else "",
        "participants": len({r.get("insider") for r in rows}),
        "transactions": len(rows),
        "total_shares": sum(share_rows) if share_rows else None,
        "total_value": round(sum(value_rows), 2) if value_rows else None,
    }


def _cluster_transactions(transactions: list[dict]) -> list[dict]:
    """Group dated buy/sell trades into adjacent same-direction windows (single-linkage on date;
    a gap over CLUSTER_GAP_DAYS starts a new window). Buys and sells never merge."""
    clusters: list[dict] = []
    for direction in ("buy", "sell"):
        rows = sorted(
            (r for r in transactions if r.get("type") == direction and _tx_date(r) is not None),
            key=_tx_date,
        )
        window: list[dict] = []
        prev = None
        for r in rows:
            d = _tx_date(r)
            if prev is not None and (d - prev).days > CLUSTER_GAP_DAYS:
                clusters.append(_make_cluster(window, direction))
                window = []
            window.append(r)
            prev = d
        if window:
            clusters.append(_make_cluster(window, direction))
    clusters.sort(key=lambda c: (c["start"], c["direction"]))
    return clusters


def _plan_summary(transactions: list[dict]) -> dict:
    """Count Rule 10b5-1 status. A missing/None flag is 'unknown' — never assumed discretionary."""
    return {
        "planned": sum(1 for t in transactions if t.get("plan_10b5_1") is True),
        "discretionary": sum(1 for t in transactions if t.get("plan_10b5_1") is False),
        "unknown": sum(1 for t in transactions if t.get("plan_10b5_1") is None),
    }


def _role_split(transactions: list[dict]) -> dict:
    """Aggregate buy/sell counts by reporting-owner role. An owner holding several roles
    (e.g. officer and director) is counted under each."""
    split = {
        "officer": {"buys": 0, "sells": 0},
        "director": {"buys": 0, "sells": 0},
        "ten_percent_owner": {"buys": 0, "sells": 0},
    }
    role_field = {
        "officer": "is_officer",
        "director": "is_director",
        "ten_percent_owner": "is_ten_percent_owner",
    }
    for t in transactions:
        if t.get("type") == "buy":
            key = "buys"
        elif t.get("type") == "sell":
            key = "sells"
        else:
            continue
        for role, field in role_field.items():
            if t.get(field):
                split[role][key] += 1
    return split


def analyze_insider_patterns(transactions: list[dict]) -> dict:
    """Pure analytics over parsed Form 4 transactions: clustered buy/sell windows, a 10b5-1 plan
    summary, and an officer/director/10%-owner role split. No I/O — safe to unit-test directly."""
    return {
        "clusters": _cluster_transactions(transactions),
        "plan_summary": _plan_summary(transactions),
        "role_split": _role_split(transactions),
    }


def insider_patterns(session, workspace_id: str) -> dict:
    """Insider-pattern analytics built on the same parsed Form 4 feed as insiders().

    Preserves the explicit source_status contract: an unavailable feed yields an 'unavailable'
    status, never a false-clean empty result.
    """
    ins = insiders(session, workspace_id)
    analysis = analyze_insider_patterns(ins["transactions"])
    return {
        "workspace_id": workspace_id,
        "clusters": analysis["clusters"],
        "plan_summary": analysis["plan_summary"],
        "role_split": analysis["role_split"],
        "source_status": ins["source_status"],
        "source_error": ins["source_error"],
        "generated_at": now_utc(),
    }


# --- Themes (EDGAR full-text search) ---------------------------------------
def _efts_search(phrase: str, cik10: str) -> dict | None:
    try:
        with httpx.Client(timeout=30, headers=_headers(), follow_redirects=True) as c:
            resp = c.get(EFTS_URL, params={"q": f'"{phrase}"', "ciks": cik10})
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("themes: EFTS search failed for '%s': %s", phrase, exc)
        return None


def _hit_url(cik10: str, adsh_doc: str) -> str | None:
    """Build an archive URL from an EFTS hit _id of the form 'accession:document.htm'."""
    if not adsh_doc:
        return None
    parts = adsh_doc.split(":", 1)
    accession = parts[0]
    doc = parts[1] if len(parts) > 1 else ""
    return _archive_url(cik10, accession, doc)


def themes(session, workspace_id: str) -> dict:
    target = _target_with_cik(session, workspace_id)
    cik10 = _cik10(target.cik)

    out: list[dict] = []
    failures = 0
    for key, label, phrase in THEMES:
        data = _efts_search(phrase, cik10)
        if data is None:
            failures += 1
            out.append({"theme": key, "label": label, "count": None, "hits": []})
            edgar_client.polite_pause()
            continue
        hits_node = (data.get("hits") or {})
        total = ((hits_node.get("total") or {}).get("value")) or 0
        hit_rows: list[dict] = []
        for h in (hits_node.get("hits") or [])[:3]:
            src = h.get("_source") or {}
            forms = src.get("form") or src.get("root_form") or ""
            form = forms[0] if isinstance(forms, list) and forms else (forms or "filing")
            hit_rows.append(
                {
                    "form": form,
                    "date": src.get("file_date") or "",
                    "url": _hit_url(cik10, h.get("_id") or ""),
                }
            )
        out.append({"theme": key, "label": label, "count": int(total), "hits": hit_rows})
        edgar_client.polite_pause()
    source_status = (
        "unavailable" if failures == len(THEMES) else "partial" if failures else "available"
    )
    return {
        "workspace_id": workspace_id,
        "themes": out,
        "source_status": source_status,
        "source_error": (
            f"{failures} of {len(THEMES)} SEC full-text searches were unavailable."
            if failures
            else None
        ),
        "generated_at": now_utc(),
    }


# --- SIC auto-peer discovery -----------------------------------------------
_CIK_TAG = re.compile(r"<CIK>(\d{1,10})</CIK>", re.IGNORECASE)


def _peers_by_sic(sic: str, exclude_cik10: str, limit: int = 6) -> list[str]:
    """Discover same-SIC public peers that have a ticker in SEC's ticker map. Best-effort."""
    if not sic:
        return []
    try:
        with httpx.Client(timeout=30, headers=_headers(), follow_redirects=True) as c:
            resp = c.get(
                BROWSE_EDGAR,
                params={
                    "action": "getcompany",
                    "SIC": sic,
                    "type": "10-K",
                    "dateb": "",
                    "owner": "include",
                    "count": "100",
                    "output": "atom",
                },
            )
            resp.raise_for_status()
            text = resp.text
    except httpx.HTTPError as exc:
        logger.warning("auto-comps: SIC browse failed for %s: %s", sic, exc)
        return []

    # Reverse ticker map (cik10 -> ticker) so we only keep filers with a resolvable public ticker.
    try:
        cik_to_ticker: dict[str, str] = {}
        for info in edgar_client._ticker_map().values():  # noqa: SLF001 - internal reuse, keyless map
            cik_to_ticker.setdefault(info["cik"], info["ticker"])
    except EdgarError:
        return []

    tickers: list[str] = []
    seen: set[str] = set()
    for m in _CIK_TAG.finditer(text):
        cik10 = m.group(1).zfill(10)
        if cik10 == exclude_cik10 or cik10 in seen:
            continue
        seen.add(cik10)
        tk = cik_to_ticker.get(cik10)
        if tk:
            tickers.append(tk)
        if len(tickers) >= limit:
            break
    return tickers


def auto_comps(session, workspace_id: str) -> dict:
    """Discover same-SIC public peers and add them as comps. Returns comps + a discovery note."""
    target = _target_with_cik(session, workspace_id)
    cik10 = _cik10(target.cik)
    try:
        sub = edgar_client.get_submissions(cik10)
    except EdgarError as exc:
        raise NotFound(f"Could not read SEC submissions for the target: {exc}") from exc

    sic = str(sub.get("sic") or "")
    sic_desc = sub.get("sicDescription") or target.sector
    tickers = _peers_by_sic(sic, cik10)

    added_note: str
    if tickers:
        bench.add_comps_by_ticker(session, workspace_id, tickers)
        added_note = (
            f"Discovered {len(tickers)} same-SIC peer(s) (SIC {sic} — {sic_desc}) via EDGAR: "
            f"{', '.join(tickers)}."
        )
    else:
        added_note = (
            f"No same-SIC public peers with resolvable tickers were found for SIC {sic} "
            f"({sic_desc}); add peers by ticker manually."
        )
    comps = bench.list_comps(session, workspace_id)
    return {"comps": comps, "note": added_note}


# --- Red-flag findings (spliced into analysis by the integration agent) -----
def _finding(cat, label, title, finding, severity, score, conf, ws, followup, evidence) -> dict:
    return {
        "risk_category": cat,
        "risk_category_label": label,
        "title": title,
        "finding": finding,
        "severity": severity,
        "severity_score": score,
        "likelihood": "high" if score >= 6 else "medium",
        "confidence": conf,
        "workstream_owner": ws,
        "follow_up_question": followup,
        "evidence": evidence,
    }


def risk_flags(session, workspace_id: str) -> list[dict]:
    """Deterministic red flags from SEC event/insider feeds, same shape as RiskAnalyst.financial_flags.

    Degrades to [] on any network/data problem so it never breaks the analysis pipeline.
    """
    target = get_target(session, workspace_id)
    if target is None or not target.cik:
        return []
    name = target.name
    flags: list[dict] = []

    # 1) Significant 8-K events: non-reliance/restatement (4.02) or auditor change (4.01).
    try:
        ev = events(session, workspace_id)
    except (NotFound, EdgarError):
        ev = {"events": []}
    for e in ev["events"]:
        codes = {it["code"] for it in e["items"]}
        if "4.02" in codes:
            flags.append(_finding(
                "legal_regulatory", "Legal / regulatory",
                "Non-reliance (restatement) 8-K filed",
                f"{name} filed an Item 4.02 8-K on {e['date']} indicating non-reliance on previously issued "
                f"financial statements — a restatement signal that undermines reported-figure reliability and "
                f"raises controls/governance concerns.",
                "high", 7, 0.9, "legal_regulatory",
                "What triggered the non-reliance, which periods/line items are affected, and what is the "
                "remediation and restatement timeline?",
                _event_evidence(
                    f"{name} disclosed non-reliance on prior financials (Item 4.02) on {e['date']}.",
                    f"SEC 8-K Item 4.02 filed {e['date']} ({e['form']}).", e, 0.9,
                ),
            ))
            break  # one restatement flag is enough
    for e in ev["events"]:
        codes = {it["code"] for it in e["items"]}
        if "4.01" in codes and not any(f["title"].startswith("Non-reliance") for f in flags):
            flags.append(_finding(
                "legal_regulatory", "Legal / regulatory",
                "Auditor change (Item 4.01) 8-K filed",
                f"{name} filed an Item 4.01 8-K on {e['date']} reporting a change in its certifying accountant; "
                f"an auditor change can precede disagreements or control issues and warrants scrutiny.",
                "high", 6, 0.82, "legal_regulatory",
                "Was the auditor change accompanied by any disagreements or reportable events, and who is the "
                "successor auditor?",
                _event_evidence(
                    f"{name} reported an auditor change (Item 4.01) on {e['date']}.",
                    f"SEC 8-K Item 4.01 filed {e['date']} ({e['form']}).", e, 0.82,
                ),
            ))
            break

    # 2) Heavy insider net selling over the trailing window -> management flag.
    try:
        ins = insiders(session, workspace_id)
    except (NotFound, EdgarError):
        ins = {"summary": {"buys": 0, "sells": 0, "net_shares": None, "window_days": INSIDER_WINDOW_DAYS}}
    s = ins["summary"]
    net = s.get("net_shares")
    if net is not None and net < 0 and s.get("sells", 0) > max(1, s.get("buys", 0)):
        flags.append(_finding(
            "legal_regulatory", "Management / insider activity",
            "Net insider selling over the trailing 90 days",
            f"Insiders at {name} were net sellers over the last {s['window_days']} days "
            f"({s['sells']} sell vs. {s['buys']} buy transaction(s), net {abs(net):,.0f} shares disposed). "
            f"Concentrated insider selling can signal weaker management conviction and warrants context.",
            "medium", 5, 0.7, "management",
            "What is the context for recent insider sales (10b5-1 plans, tax/diversification vs. discretionary), "
            "and how do they compare to historical patterns?",
            {
                "claim": f"{name} insiders were net sellers of {abs(net):,.0f} shares in the last {s['window_days']} days.",
                "claim_type": "calculation",
                "evidence_text": (
                    f"SEC Form 4 filings (trailing {s['window_days']} days): {s['sells']} sell vs. {s['buys']} "
                    f"buy transactions; net {net:,.0f} shares."
                ),
                "source_name": f"{name} SEC Form 4 filings",
                "source_type": "sec_filing",
                "source_url": "https://www.sec.gov/cgi-bin/browse-edgar",
                "source_date": None,
                "source_section": "Insider transactions (Form 4)",
                "confidence": 0.7,
                "agent_name": "sec_feeds",
            },
        ))
    return flags


def _event_evidence(claim: str, text: str, event: dict, conf: float) -> dict:
    return {
        "claim": claim,
        "claim_type": "fact",
        "evidence_text": text,
        "source_name": f"SEC {event['form']} ({event['date']})",
        "source_type": "sec_filing",
        "source_url": event.get("url"),
        "source_date": event.get("date"),
        "source_section": "8-K current report",
        "confidence": conf,
        "agent_name": "sec_feeds",
    }
