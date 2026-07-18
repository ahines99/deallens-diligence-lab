"""G65 — Sum-of-the-parts valuation from G12 segment revenue.

Per-segment EV = latest-period segment revenue x a USER-SUPPLIED EV/Revenue multiple. The parts
are reconciled against consolidated revenue with an EXPLICIT unallocated/eliminations residual:

* ``unallocated.revenue`` = consolidated revenue - sum(segment revenues) for the as-of period —
  reported verbatim (it can be negative for eliminations);
* the residual is VALUED only when the request supplies ``residual_multiple``; otherwise it is
  reported unvalued (``implied_ev: null``). The total is never force-balanced to the
  consolidated figure, and a segment without a supplied multiple stays unvalued too.

The G12 reconciliation status propagates: ``partial`` segment data (members that do not sum to
the consolidated total) yields a ``partial`` SOTP with the G12 note carried through, and
consolidated-only workspaces report ``unavailable`` — segment splits are never fabricated.
"""
from __future__ import annotations

from collections.abc import Mapping

from src.db.base import now_utc
from src.services import workspace_service
from src.services.common import NotFound, get_workspace_or_404

_REFRESH_NOTE = (
    "Segment XBRL extraction is not stored for this workspace — refresh (re-ingest) required."
)


def _unavailable(workspace_id: str, note: str | None) -> dict:
    return {
        "workspace_id": workspace_id,
        "status": "unavailable",
        "as_of_period": None,
        "segments": [],
        "unallocated": {"revenue": None, "multiple": None, "implied_ev": None},
        "total_implied_ev": None,
        "consolidated_revenue": None,
        "reconciliation_note": note,
        "generated_at": now_utc(),
    }


def _consolidated_revenue(financials: dict, as_of_period: str) -> float | None:
    """Consolidated revenue for the segment as-of period, from the stored XBRL extract.

    Preference order: the headline annual figure when its period end matches exactly, then the
    trends row for the same calendar year. Returns ``None`` when neither matches — the residual
    is then reported as uncomputable rather than derived from a mismatched period.
    """
    if financials.get("fiscal_year_end") == as_of_period and financials.get("revenue") is not None:
        return float(financials["revenue"])
    for row in (financials.get("trends") or {}).get("rows", []):
        if row.get("year") == as_of_period[:4] and row.get("revenue") is not None:
            return float(row["revenue"])
    return None


def _resolve_multiple(
    segment: dict, multiples: Mapping[str, float], default_multiple: float | None
) -> float | None:
    """Match a request multiple by XBRL member first, then by the human segment name."""
    for key in (segment.get("member"), segment.get("segment_name")):
        if key is not None and key in multiples:
            return float(multiples[key])
    return default_multiple


def build(session, workspace_id: str, request: Mapping) -> dict:
    """Sum-of-the-parts over the workspace's stored G12 segment revenue.

    ``request`` keys: ``multiples`` ({member-or-segment-name: EV/Revenue multiple}),
    ``default_multiple`` (optional fallback), ``residual_multiple`` (optional — the residual is
    valued ONLY when this is supplied).
    """
    get_workspace_or_404(session, workspace_id)
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")

    financials = target.financials or {}
    seg_data = financials.get("segments")
    if seg_data is None:
        return _unavailable(workspace_id, _REFRESH_NOTE)
    if seg_data.get("status") == "unavailable" or not seg_data.get("segments"):
        return _unavailable(
            workspace_id,
            seg_data.get("note")
            or "Segment detail is not available for this workspace (consolidated only).",
        )

    multiples: Mapping[str, float] = request.get("multiples") or {}
    default_multiple = request.get("default_multiple")
    residual_multiple = request.get("residual_multiple")

    # As-of period: the most recent period end reported by any segment.
    as_of_period = max(
        period["period_end"]
        for segment in seg_data["segments"]
        for period in segment.get("periods", [])
    )

    rows: list[dict] = []
    unvalued: list[str] = []
    missing_period: list[str] = []
    for segment in seg_data["segments"]:
        revenue = next(
            (
                float(period["revenue"])
                for period in segment.get("periods", [])
                if period["period_end"] == as_of_period
            ),
            None,
        )
        multiple = _resolve_multiple(segment, multiples, default_multiple)
        implied = round(revenue * multiple, 2) if (revenue is not None and multiple is not None) else None
        if revenue is None:
            missing_period.append(segment["segment_name"])
        elif multiple is None:
            unvalued.append(segment["segment_name"])
        rows.append(
            {
                "segment_name": segment["segment_name"],
                "revenue": revenue,
                "multiple": multiple,
                "implied_ev": implied,
                "source": {
                    "concept": segment.get("source_concept"),
                    "axis": seg_data.get("axis"),
                    "member": segment.get("member"),
                    "period_end": as_of_period if revenue is not None else None,
                },
            }
        )

    consolidated = _consolidated_revenue(financials, as_of_period)

    notes: list[str] = []
    if seg_data.get("status") == "partial" and seg_data.get("note"):
        notes.append(f"G12 segment reconciliation: {seg_data['note']}")
    if missing_period:
        notes.append(
            "No revenue reported for the as-of period by: "
            + ", ".join(missing_period)
            + " — these segments are excluded from the residual check, never interpolated."
        )
    if unvalued:
        notes.append(
            "No multiple supplied for: " + ", ".join(unvalued) + " — reported unvalued."
        )

    # Explicit residual: computable only when consolidated revenue exists for the same period
    # and every segment reported that period (a missing segment would corrupt the residual).
    unallocated_revenue: float | None = None
    if consolidated is None:
        notes.append(
            f"Consolidated revenue for period ending {as_of_period} is not stored; the "
            "unallocated residual cannot be computed."
        )
    elif missing_period:
        notes.append(
            "Unallocated residual not computed: at least one segment lacks the as-of period."
        )
    else:
        seg_sum = sum(row["revenue"] for row in rows)
        unallocated_revenue = round(consolidated - seg_sum, 2)
        if residual_multiple is None:
            notes.append(
                f"Unallocated/eliminations residual of {unallocated_revenue:,.0f} is reported "
                "but UNVALUED (no residual_multiple supplied) — the total is never "
                "force-balanced to the consolidated figure."
            )

    unallocated_ev = (
        round(unallocated_revenue * float(residual_multiple), 2)
        if (unallocated_revenue is not None and residual_multiple is not None)
        else None
    )

    valued = [row["implied_ev"] for row in rows if row["implied_ev"] is not None]
    if unallocated_ev is not None:
        valued.append(unallocated_ev)
    total_implied_ev = round(sum(valued), 2) if valued else None
    if total_implied_ev is None:
        notes.append("No segment could be valued — supply multiples (or a default_multiple).")

    return {
        "workspace_id": workspace_id,
        "status": seg_data.get("status", "unavailable"),
        "as_of_period": as_of_period,
        "segments": rows,
        "unallocated": {
            "revenue": unallocated_revenue,
            "multiple": float(residual_multiple) if residual_multiple is not None else None,
            "implied_ev": unallocated_ev,
        },
        "total_implied_ev": total_implied_ev,
        "consolidated_revenue": consolidated,
        "reconciliation_note": " ".join(notes) if notes else None,
        "generated_at": now_utc(),
    }
