"""G64 — XBRL frames peer benchmarking: percentile context from the SEC frames API.

The frames API (``/api/xbrl/frames/us-gaap/{concept}/USD/CY{year}.json``) returns ONE fact per
reporting entity for an annual calendar frame — the entire universe of US filers that tagged the
concept for that period. Honesty note on "peers": frame rows carry a CIK and entity name but NO
SIC classification, and keyless EDGAR has no bulk CIK->SIC endpoint — restricting the universe to
the target's 4-digit SIC would require one live submissions lookup per entity (an unbounded
fan-out we refuse). We therefore report percentile ranks against the FULL frames universe,
labeled exactly that in ``peer_scope``, with explicit coverage counts; the target's SIC code and
description (from its own submissions feed — the target row stores only the description, as
``sector``) are reported alongside for context. Thin coverage degrades to an explicit
"insufficient peer coverage" note — a percentile is never fabricated from a thin frame.

Percentile convention (midrank): ``percentile = (strictly_below + 0.5 * ties) / n`` over the peer
universe, in [0, 1]. The target's own frame row is excluded from its universe, so a target below
every peer reports 0.0 and above every peer reports 1.0. Target values come from the workspace's
STORED financials (the same XBRL extraction the rest of the app cites), not re-derived from the
frame, so the target's concept fallback may differ from the frame concept — the metric's
``concepts`` list binds exactly which frame concept(s) the universe was built from.
"""
from __future__ import annotations

import logging

from src.db.base import now_utc
from src.services import edgar_client
from src.services.common import NotFound
from src.services.edgar_client import EdgarError
from src.services.sec_financials import OPERATING_INCOME_CONCEPTS, REVENUE_CONCEPTS
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.peer_benchmark")

# Below this many computable peer values a percentile is not reported (insufficient coverage).
COVERAGE_FLOOR = 20

_SCOPE_NOTE = (
    "Percentiles are ranked against the full SEC XBRL frames universe — every US filer reporting "
    "the referenced us-gaap concept for the frame year — NOT a SIC-restricted peer set: frame "
    "rows carry no SIC classification, and building one keylessly would require a per-entity "
    "submissions fan-out. The universe skews to filers whose fiscal periods align with the "
    "calendar frame. The target's own frame row is excluded from its universe; 'coverage' counts "
    f"the peers whose value was computable, and below {COVERAGE_FLOOR} the percentile degrades "
    "to 'insufficient peer coverage' rather than being reported."
)


# --- pure math -------------------------------------------------------------
def percentile_rank(values: list[float], target: float) -> float | None:
    """Midrank percentile of ``target`` within ``values``: (below + 0.5*ties) / n, in [0, 1].

    Returns ``None`` for an empty universe — a percentile against nobody is not 0, it is absent.
    """
    if not values:
        return None
    below = sum(1 for value in values if value < target)
    ties = sum(1 for value in values if value == target)
    return round((below + 0.5 * ties) / len(values), 4)


def frame_values_by_cik(frame: dict) -> dict[int, float]:
    """Map ``cik -> reported value`` from a frames payload. Rows without a numeric val drop out."""
    out: dict[int, float] = {}
    for row in frame.get("data", []) or []:
        cik, val = row.get("cik"), row.get("val")
        if cik is None or val is None:
            continue
        try:
            out[int(cik)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def growth_universe(current: dict[int, float], prior: dict[int, float]) -> list[float]:
    """Per-entity YoY growth for entities present in BOTH frame years with positive prior value.

    An entity missing either year is excluded (its growth is unknown, never imputed); a zero or
    negative prior makes the ratio meaningless and likewise excludes the entity.
    """
    return [
        (current[cik] - prior[cik]) / prior[cik]
        for cik in current.keys() & prior.keys()
        if prior[cik] > 0
    ]


def margin_universe(numerator: dict[int, float], revenue: dict[int, float]) -> list[float]:
    """Per-entity margin for entities present in BOTH frames with positive revenue."""
    return [
        numerator[cik] / revenue[cik]
        for cik in numerator.keys() & revenue.keys()
        if revenue[cik] > 0
    ]


# --- metric + payload assembly ---------------------------------------------
def _metric(
    metric: str,
    target_value: float | None,
    universe: list[float],
    concepts: list[str],
    basis: str,
) -> dict:
    coverage = len(universe)
    notes = [basis]
    if target_value is None:
        notes.append("target value unavailable in stored financials")
    if coverage < COVERAGE_FLOOR:
        notes.append(
            f"insufficient peer coverage: {coverage} peer(s) reporting, below the floor of "
            f"{COVERAGE_FLOOR} — percentile not reported"
        )
    percentile = None
    if target_value is not None and coverage >= COVERAGE_FLOOR:
        percentile = percentile_rank(universe, target_value)
    return {
        "metric": metric,
        "target_value": target_value,
        "percentile": percentile,
        "coverage": coverage,
        "concepts": concepts,
        "note": "; ".join(notes),
    }


def _payload(workspace_id: str, target_name: str, **overrides) -> dict:
    base = {
        "workspace_id": workspace_id,
        "target_name": target_name,
        "status": "unavailable",
        "as_of_year": None,
        "target_sic": None,
        "sic_description": None,
        "peer_scope": None,
        "metrics": [],
        "note": _SCOPE_NOTE,
        "source_error": None,
        "generated_at": now_utc(),
    }
    base.update(overrides)
    return base


def build(session, workspace_id: str) -> dict:
    """Frames-universe percentile benchmark for the workspace target, computed on demand.

    Degrades to ``status: "unavailable"`` (with ``source_error``) on any EDGAR outage and to
    ``"partial"`` when a metric's percentile could not be honestly reported — never false-clean.
    """
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    if not target.cik:
        raise NotFound(
            "Target has no SEC CIK; peer benchmarking requires a public (EDGAR) company."
        )
    target_cik = int(target.cik)
    cik10 = str(target.cik).lstrip("0").zfill(10)

    trend_years = ((target.financials or {}).get("trends") or {}).get("years") or []
    if not trend_years:
        return _payload(
            workspace_id,
            target.name,
            source_error=(
                "Stored financials carry no annual trend years to anchor a frame year — "
                "refresh (re-ingest) required."
            ),
        )
    as_of_year = int(trend_years[-1])

    # Target SIC context. The target row stores only the SIC description (as ``sector``); the
    # numeric code lives in the submissions feed, which is cached by the shared client.
    try:
        submissions = edgar_client.get_submissions(cik10)
    except EdgarError as exc:
        logger.warning("peer_benchmark: submissions fetch failed for %s: %s", cik10, exc)
        return _payload(
            workspace_id,
            target.name,
            as_of_year=as_of_year,
            source_error="SEC EDGAR submissions are temporarily unavailable.",
        )
    target_sic = str(submissions.get("sic") or "").strip() or None
    sic_description = (
        str(submissions.get("sicDescription") or "").strip() or target.sector or None
    )

    # Revenue frames: the first Revenues-family concept with data in BOTH frame years. The same
    # concept is used for both years — concepts are never mixed across a growth calculation.
    revenue_concept: str | None = None
    revenue_current: dict[int, float] = {}
    revenue_prior: dict[int, float] = {}
    frame_errors: list[str] = []
    for concept in REVENUE_CONCEPTS:
        try:
            current = frame_values_by_cik(edgar_client.frames_annual(concept, as_of_year))
            prior = frame_values_by_cik(edgar_client.frames_annual(concept, as_of_year - 1))
        except EdgarError as exc:
            frame_errors.append(f"{concept}: {exc}")
            continue
        if current and prior:
            revenue_concept, revenue_current, revenue_prior = concept, current, prior
            break
    if revenue_concept is None:
        return _payload(
            workspace_id,
            target.name,
            as_of_year=as_of_year,
            target_sic=target_sic,
            sic_description=sic_description,
            source_error=(
                "; ".join(frame_errors)
                if frame_errors
                else (
                    f"No Revenues-family frames carry data for CY{as_of_year}/CY{as_of_year - 1}."
                )
            ),
        )
    revenue_current.pop(target_cik, None)
    revenue_prior.pop(target_cik, None)

    metrics = [
        _metric(
            "revenue_growth",
            target.revenue_growth,
            growth_universe(revenue_current, revenue_prior),
            [revenue_concept],
            basis=(
                f"YoY growth CY{as_of_year} vs CY{as_of_year - 1} per entity present in both "
                f"{revenue_concept} frame years with positive prior revenue"
            ),
        )
    ]

    # Operating margin: entities present in BOTH the operating-income and revenue frames for the
    # same year. A frames outage for the numerator degrades this one metric, never the payload.
    oi_concept: str | None = None
    oi_values: dict[int, float] = {}
    oi_errors: list[str] = []
    for concept in OPERATING_INCOME_CONCEPTS:
        try:
            values = frame_values_by_cik(edgar_client.frames_annual(concept, as_of_year))
        except EdgarError as exc:
            oi_errors.append(f"{concept}: {exc}")
            continue
        if values:
            oi_concept, oi_values = concept, values
            break
    if oi_concept is None:
        metrics.append(
            {
                "metric": "operating_margin",
                "target_value": target.operating_margin,
                "percentile": None,
                "coverage": 0,
                "concepts": list(OPERATING_INCOME_CONCEPTS),
                "note": (
                    f"operating-income frames unavailable for CY{as_of_year}"
                    + (f": {'; '.join(oi_errors)}" if oi_errors else " (no data)")
                ),
            }
        )
    else:
        oi_values.pop(target_cik, None)
        metrics.append(
            _metric(
                "operating_margin",
                target.operating_margin,
                margin_universe(oi_values, revenue_current),
                [oi_concept, revenue_concept],
                basis=(
                    f"{oi_concept} / {revenue_concept} per entity present in BOTH CY{as_of_year} "
                    "frames with positive revenue"
                ),
            )
        )

    status = "available" if all(m["percentile"] is not None for m in metrics) else "partial"
    return _payload(
        workspace_id,
        target.name,
        status=status,
        as_of_year=as_of_year,
        target_sic=target_sic,
        sic_description=sic_description,
        peer_scope=(
            f"all US filers reporting {revenue_concept} for CY{as_of_year} "
            "(SEC XBRL frames universe; not SIC-restricted)"
        ),
        metrics=metrics,
        source_error="; ".join(oi_errors) or None,
    )
