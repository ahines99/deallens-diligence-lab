"""G66 — buyback & dilution analysis from XBRL company facts.

Per fiscal year: shares outstanding (annual instant), share-based-compensation expense,
common-stock repurchases, and the net YoY share-count change. Period keying is the CY-frame
discipline from ``sec_financials``: annual duration facts are labeled by their ``CY####`` frame —
``fy`` is the fiscal year of the *reporting filing* (every comparative restated in one 10-K
shares it) and is never a period key. Annual instants are labeled via the exact fiscal-year
balance-sheet dates taken from the revenue durations, mirroring ``extract_forensic_inputs``.

Raw company facts are not persisted at ingestion (only derived extracts are stored on the
target), so facts are fetched on demand for the target CIK through ``edgar_client`` — the same
degraded-source discipline as the other extension feeds: an EDGAR outage reports
``status: "unavailable"`` with a ``source_error``, never a false-clean empty.

Never-impute: a concept the filer did not tag for a year stays ``None`` for that field-year and
is called out in the note — nothing is interpolated, and ``net_dilution_pct`` is only derived
from two CONSECUTIVE tagged fiscal years (never across a gap). Sign convention:
``net_dilution_pct`` > 0 means the share count grew year over year (net dilution); < 0 means net
reduction (repurchases retiring more than issuance). ``repurchases``
(PaymentsForRepurchaseOfCommonStock) is a cash outflow reported as a positive number.
"""
from __future__ import annotations

import logging

from src.db.base import now_utc
from src.services import edgar_client, sec_financials
from src.services.common import NotFound
from src.services.edgar_client import EdgarError

# The same annual-instant share-count fallback list the forensic extractor uses.
from src.services.sec_financials import _SHARES_INSTANT
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.dilution")

SBC_CONCEPTS = ["ShareBasedCompensation"]
REPURCHASE_CONCEPTS = ["PaymentsForRepurchaseOfCommonStock"]

_FIELDS = ("shares_out", "sbc", "repurchases")
_MAX_YEARS = 8


def _points_by_year(
    facts: dict,
    concepts: list[str],
    *,
    instant: bool,
    unit: str,
    duration_periods: dict[str, str],
) -> tuple[str | None, dict[str, dict]]:
    """(concept, {fiscal-year label -> latest-filed point}) for the first concept with data.

    Duration points are labeled by their ``CY####`` frame (``sec_financials._period_year``);
    instant points are labeled via the fiscal-year balance-sheet dates from the revenue
    durations. An instant whose date matches no revenue duration end is dropped — its year is
    unknown and is never guessed.
    """
    concept, points = edgar_client.pick_concept(facts, concepts, instant=instant, unit=unit)
    if concept is None:
        return None, {}
    labeled: dict[str, dict] = {}
    for point in points:
        if instant:
            year = duration_periods.get(point.get("end", ""))
        else:
            year = sec_financials._period_year(point)
        if year:
            labeled[year] = point
    return concept, labeled


def build(facts: dict, n: int = _MAX_YEARS) -> dict:
    """Per-fiscal-year buyback/dilution derivation over raw XBRL company facts (pure)."""
    duration_periods = sec_financials._duration_periods(facts)
    shares_concept, shares = _points_by_year(
        facts, _SHARES_INSTANT, instant=True, unit="shares", duration_periods=duration_periods
    )
    sbc_concept, sbc = _points_by_year(
        facts, SBC_CONCEPTS, instant=False, unit="USD", duration_periods=duration_periods
    )
    repurchase_concept, repurchases = _points_by_year(
        facts, REPURCHASE_CONCEPTS, instant=False, unit="USD", duration_periods=duration_periods
    )
    per_field: dict[str, dict[str, dict]] = {
        "shares_out": shares,
        "sbc": sbc,
        "repurchases": repurchases,
    }
    sources = {
        "shares_out": shares_concept,
        "sbc": sbc_concept,
        "repurchases": repurchase_concept,
    }

    years = sorted(set().union(*(set(labeled) for labeled in per_field.values())))[-n:]
    if not years:
        return {
            "status": "unavailable",
            "years": [],
            "by_year": {},
            "citations": {},
            "sources": sources,
            "note": (
                "No shares-outstanding, share-based-compensation, or common-stock-repurchase "
                "concepts are tagged in company facts — nothing to derive, nothing imputed."
            ),
        }

    by_year: dict[str, dict] = {}
    citations: dict[str, dict] = {}
    missing: dict[str, list[str]] = {field: [] for field in _FIELDS}
    for year in years:
        row: dict[str, float | None] = {}
        cites: dict[str, dict] = {}
        for field, labeled in per_field.items():
            point = labeled.get(year)
            if point is None:
                row[field] = None
                missing[field].append(year)
                continue
            row[field] = float(point["val"])
            cites[field] = {
                "concept": sources[field],
                "end": point.get("end") or None,
                "accession": point.get("accn") or None,
                "form": point.get("form") or None,
            }
        current = shares.get(year)
        prior = shares.get(str(int(year) - 1))  # the CONSECUTIVE prior fiscal year, or nothing
        if current is not None and prior is not None and float(prior["val"]):
            row["net_dilution_pct"] = round(
                (float(current["val"]) - float(prior["val"])) / float(prior["val"]), 4
            )
        else:
            row["net_dilution_pct"] = None
        by_year[year] = row
        citations[year] = cites

    convention = (
        f"net_dilution_pct is the YoY change in {shares_concept or 'shares outstanding'} "
        "between consecutive tagged fiscal years (positive = net dilution, negative = net "
        "reduction); repurchases are cash outflows reported positive."
    )
    gaps = [f"{field} ({', '.join(missing[field])})" for field in _FIELDS if missing[field]]
    if gaps:
        status = "partial"
        note = (
            "Untagged concept-years stay None and are never interpolated — missing: "
            + "; ".join(gaps)
            + ". "
            + convention
        )
    else:
        status = "available"
        note = f"All three concept families are tagged for every reported year. {convention}"
    return {
        "status": status,
        "years": years,
        "by_year": by_year,
        "citations": citations,
        "sources": sources,
        "note": note,
    }


def dilution(session, workspace_id: str) -> dict:
    """Buyback/dilution analysis for the workspace target, fetching facts on demand."""
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    if not target.cik:
        raise NotFound(
            "Target has no SEC CIK; dilution analysis requires a public (EDGAR) company."
        )
    cik10 = str(target.cik).lstrip("0").zfill(10)
    base = {
        "workspace_id": workspace_id,
        "target_name": target.name,
        "generated_at": now_utc(),
    }
    try:
        facts = edgar_client.get_company_facts(cik10)
    except EdgarError as exc:
        logger.warning("dilution: company facts fetch failed for %s: %s", cik10, exc)
        return {
            **base,
            "status": "unavailable",
            "years": [],
            "by_year": {},
            "citations": {},
            "sources": {field: None for field in _FIELDS},
            "note": "Dilution analysis could not be computed; SEC EDGAR is unavailable.",
            "source_error": "SEC EDGAR company facts are temporarily unavailable.",
        }
    return {**base, **build(facts), "source_error": None}
