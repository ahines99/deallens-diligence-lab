"""GovCon diligence via USAspending.gov (keyless public API).

Given a recipient (company) name, pulls federal CONTRACT award history and derives:
- total obligations + award count,
- agency concentration (top agency's share — a key GovCon risk),
- recompete exposure (top awards whose period of performance ends within ~24 months),
- incumbent view (the awards where the company must defend its position at recompete).

All figures are real. This is the defense/GovCon extension (Release 0.5).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger("deallens.usaspending")

BASE = "https://api.usaspending.gov/api/v2"
CONTRACT_TYPES = ["A", "B", "C", "D"]  # definitive contract awards (exclude IDV ceilings)
RECOMPETE_WINDOW_DAYS = 730  # ~24 months


class UsaSpendingError(Exception):
    """Raised when USAspending is unreachable or returns unexpected data."""


def _post(path: str, payload: dict) -> dict:
    try:
        with httpx.Client(timeout=30) as c:
            resp = c.post(f"{BASE}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise UsaSpendingError(f"USAspending request failed for {path}: {exc}") from exc


def clean_recipient(name: str) -> str:
    """Trim corporate suffixes that hurt the fuzzy recipient search."""
    n = name.upper()
    for suffix in (" HOLDING CORP", " HOLDINGS INC", " HOLDINGS", " HOLDING", " CORPORATION",
                   " CORP", ", INC.", " INC.", " INC", " CO.", " COMPANY", " PLC", " LP", " LLC"):
        n = n.replace(suffix, "")
    return n.strip().title()


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def award_profile(recipient_name: str) -> dict:
    name = clean_recipient(recipient_name)
    filters = {"recipient_search_text": [name], "award_type_codes": CONTRACT_TYPES}

    agencies = _post(
        "/search/spending_by_category/awarding_agency/", {"filters": filters, "limit": 10}
    ).get("results", [])
    total = sum(a.get("amount", 0) or 0 for a in agencies)

    count_res = _post("/search/spending_by_award_count/", {"filters": filters}).get("results", {})
    award_count = int(count_res.get("contracts", 0) or 0) if isinstance(count_res, dict) else 0

    fields = [
        "Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
        "Award Amount", "Description", "Period of Performance Current End Date",
        "Period of Performance Start Date",
    ]
    # Fetch a wider set (25) so recompete scanning catches awards that carry a PoP end date,
    # even when the very largest awards (often parent vehicles) leave it null.
    awards_raw = _post(
        "/search/spending_by_award/",
        {"filters": filters, "fields": fields, "sort": "Award Amount", "order": "desc", "limit": 25},
    ).get("results", [])

    scanned = []
    for a in awards_raw:
        scanned.append(
            {
                "award_id": a.get("Award ID"),
                "recipient": a.get("Recipient Name"),
                "agency": a.get("Awarding Agency"),
                "sub_agency": a.get("Awarding Sub Agency"),
                "amount": a.get("Award Amount"),
                "description": (a.get("Description") or "")[:220],
                "pop_end": a.get("Period of Performance Current End Date"),
                "pop_start": a.get("Period of Performance Start Date"),
            }
        )
    top_awards = scanned[:10]  # store the 10 largest for display

    # Agency concentration (share of obligations by the top agency).
    agency_rows = []
    for a in agencies:
        amt = a.get("amount", 0) or 0
        agency_rows.append(
            {"agency": a.get("name"), "amount": amt, "pct": round(amt / total, 4) if total else None}
        )
    top_agency = agency_rows[0] if agency_rows else None
    top_agency_pct = top_agency["pct"] if top_agency else None

    # Recompete exposure: top awards ending within the recompete window.
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=RECOMPETE_WINDOW_DAYS)
    recompetes = []
    recompete_value = 0.0
    for a in scanned:
        end = _parse_date(a["pop_end"])
        if end and today <= end <= horizon:
            recompetes.append(
                {"award_id": a["award_id"], "agency": a["agency"], "amount": a["amount"], "pop_end": a["pop_end"]}
            )
            recompete_value += a["amount"] or 0

    return {
        "recipient_name": name,
        "total_obligations": total,
        "award_count": award_count,
        "agency_concentration": agency_rows,
        "top_agency": top_agency["agency"] if top_agency else None,
        "top_agency_pct": top_agency_pct,
        "top_awards": top_awards,
        "recompete_within_24mo": {
            "count": len(recompetes),
            "value": recompete_value,
            "awards": recompetes,
        },
    }
