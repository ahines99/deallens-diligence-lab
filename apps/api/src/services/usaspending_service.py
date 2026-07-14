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
PAGE_SIZE = 100
MAX_PAGES = 250


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


def _all_pages(path: str, payload: dict) -> list[dict]:
    """Read every result page or fail rather than presenting a partial result as complete."""

    page = 1
    rows: list[dict] = []
    previous_page: list[dict] | None = None
    while page <= MAX_PAGES:
        request = {**payload, "limit": payload.get("limit", PAGE_SIZE), "page": page}
        response = _post(path, request)
        if not isinstance(response, dict):
            raise UsaSpendingError(f"USAspending returned a malformed response for {path}")
        result_page = response.get("results", [])
        if not isinstance(result_page, list):
            raise UsaSpendingError(f"USAspending returned malformed paginated results for {path}")
        if previous_page is not None and result_page and result_page == previous_page:
            raise UsaSpendingError(f"USAspending pagination did not advance for {path}")
        rows.extend(result_page)

        metadata = response.get("page_metadata") or {}
        if not isinstance(metadata, dict):
            raise UsaSpendingError(
                f"USAspending returned malformed pagination metadata for {path}"
            )
        has_next: bool | None = None
        for key in ("hasNext", "has_next_page", "has_next"):
            if key in metadata:
                has_next = bool(metadata[key])
                break
        if has_next is None and metadata.get("next") is not None:
            has_next = True
        if has_next is None:
            has_next = len(result_page) >= request["limit"]
        if not has_next:
            return rows
        if not result_page:
            raise UsaSpendingError(
                f"USAspending claimed another page after an empty result page for {path}"
            )

        previous_page = result_page
        try:
            next_page = int(metadata.get("next") or (page + 1))
        except (TypeError, ValueError) as exc:
            raise UsaSpendingError(
                f"USAspending returned an invalid next page for {path}"
            ) from exc
        if next_page <= page:
            raise UsaSpendingError(f"USAspending pagination did not advance for {path}")
        page = next_page
    raise UsaSpendingError(
        f"USAspending pagination exceeded the {MAX_PAGES}-page safety limit for {path}"
    )


def _number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    agencies = _all_pages(
        "/search/spending_by_category/awarding_agency/",
        {"filters": filters, "limit": PAGE_SIZE},
    )
    agency_amounts = [_number(agency.get("amount")) for agency in agencies]
    if any(amount is None for amount in agency_amounts):
        raise UsaSpendingError("USAspending returned an agency row without a numeric obligation amount")
    total = sum(amount for amount in agency_amounts if amount is not None)

    count_res = _post("/search/spending_by_award_count/", {"filters": filters}).get("results")
    if not isinstance(count_res, dict) or count_res.get("contracts") is None:
        raise UsaSpendingError("USAspending returned no authoritative contract award count")
    try:
        award_count = int(count_res["contracts"])
    except (TypeError, ValueError) as exc:
        raise UsaSpendingError(
            "USAspending returned a non-numeric authoritative contract award count"
        ) from exc

    fields = [
        "Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
        "Award Amount", "Description", "Period of Performance Current End Date",
        "Period of Performance Start Date",
    ]
    awards_raw = _all_pages(
        "/search/spending_by_award/",
        {
            "filters": filters,
            "fields": fields,
            "sort": "Award Amount",
            "order": "desc",
            "limit": PAGE_SIZE,
        },
    )

    scanned_by_id: dict[str, dict] = {}
    for a in awards_raw:
        award = {
            "award_id": a.get("Award ID"),
            "recipient": a.get("Recipient Name"),
            "agency": a.get("Awarding Agency"),
            "sub_agency": a.get("Awarding Sub Agency"),
            "amount": _number(a.get("Award Amount")),
            "description": (a.get("Description") or "")[:220],
            "pop_end": a.get("Period of Performance Current End Date"),
            "pop_start": a.get("Period of Performance Start Date"),
        }
        identity = str(
            award["award_id"]
            or (
                award["recipient"],
                award["agency"],
                award["amount"],
                award["pop_start"],
                award["pop_end"],
            )
        )
        scanned_by_id.setdefault(identity, award)
    scanned = sorted(
        scanned_by_id.values(),
        key=lambda award: award["amount"] if award["amount"] is not None else float("-inf"),
        reverse=True,
    )
    top_awards = scanned[:10]  # store the 10 largest for display

    # Agency concentration (share of obligations by the top agency).
    agency_rows = []
    for a in agencies:
        amt = _number(a.get("amount"))
        if amt is None:
            raise UsaSpendingError(
                "USAspending returned an agency row without a numeric obligation amount"
            )
        agency_rows.append(
            {
                "agency": a.get("name") or a.get("category"),
                "amount": amt,
                "pct": round(amt / total, 4) if total else None,
            }
        )
    agency_rows.sort(key=lambda agency: agency["amount"], reverse=True)
    top_agency = agency_rows[0] if agency_rows else None
    top_agency_pct = top_agency["pct"] if top_agency else None

    # Recompete exposure: every unique award ending within the recompete window.
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=RECOMPETE_WINDOW_DAYS)
    recompetes = []
    recompete_value = 0.0
    for a in scanned:
        end = _parse_date(a["pop_end"])
        if end and today <= end <= horizon:
            if a["amount"] is None:
                raise UsaSpendingError(
                    "USAspending returned an in-window award without a numeric award amount"
                )
            recompetes.append(
                {"award_id": a["award_id"], "agency": a["agency"], "amount": a["amount"], "pop_end": a["pop_end"]}
            )
            recompete_value += a["amount"]

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
