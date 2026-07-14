"""FRED macro overlay via the keyless fredgraph CSV endpoint (no API key required).

Provides a small set of macro series and maps a target's SEC sector to the most relevant ones,
so the diligence pack can note macro sensitivity with real, current data.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger("deallens.fred")

FREDGRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# Curated macro series (id -> label, unit, "higher is" direction for commentary).
SERIES = {
    "FEDFUNDS": {"label": "Federal funds rate", "unit": "pct", "frequency": "monthly",
                 "note": "Cost of capital / discount-rate pressure"},
    "DGS10": {"label": "10-year Treasury yield", "unit": "pct", "frequency": "daily",
               "note": "Long-rate / valuation discount proxy"},
    "CPIAUCSL": {"label": "CPI (all urban)", "unit": "index", "frequency": "monthly",
                  "note": "Inflation / input-cost pressure"},
    "UNRATE": {"label": "Unemployment rate", "unit": "pct", "frequency": "monthly",
                "note": "Labor market / demand proxy"},
    "INDPRO": {"label": "Industrial production", "unit": "index", "frequency": "monthly",
                "note": "Industrial/manufacturing demand"},
    "GDPC1": {"label": "Real GDP", "unit": "index", "frequency": "quarterly",
               "note": "Broad demand backdrop"},
}

# Sector keyword -> extra series that are especially relevant.
_SECTOR_SERIES = [
    (("manufactur", "industrial", "machinery", "semiconductor", "hardware", "electronic"), ["INDPRO"]),
    (("bank", "financ", "insurance", "real estate", "reit"), ["DGS10", "UNRATE"]),
    (("retail", "consumer", "apparel", "restaurant", "food"), ["UNRATE", "CPIAUCSL"]),
    (("software", "prepackaged", "services-computer", "internet"), ["DGS10"]),
]
# Always-relevant baseline series for any company.
_BASELINE = ["FEDFUNDS", "CPIAUCSL"]


def sectors_series(sector: str) -> list[str]:
    s = (sector or "").lower()
    ids = list(_BASELINE)
    for keywords, extra in _SECTOR_SERIES:
        if any(k in s for k in keywords):
            for e in extra:
                if e not in ids:
                    ids.append(e)
    return ids


def _fetch_series(series_id: str) -> dict | None:
    try:
        with httpx.Client(timeout=15) as c:
            resp = c.get(FREDGRAPH.format(series_id=series_id))
            resp.raise_for_status()
            lines = resp.text.strip().splitlines()
    except httpx.HTTPError as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        return None
    if len(lines) < 2:
        return None
    points: list[dict] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        date, val = parts
        if val in ("", "."):
            continue
        try:
            points.append({"date": date, "value": float(val)})
        except ValueError:
            continue
    return _summarize_series(series_id, points)


def _anniversary(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # February 29 -> February 28 in the prior year.
        return day.replace(year=day.year - 1, day=28)


def _summarize_series(series_id: str, points: list[dict]) -> dict | None:
    """Build a five-year view and compare the latest point to its dated prior-year peer."""

    dated: dict[date, float] = {}
    for point in points:
        try:
            observation_date = date.fromisoformat(str(point.get("date", ""))[:10])
            value = float(point["value"])
        except (TypeError, ValueError, KeyError):
            continue
        dated[observation_date] = value
    if not dated:
        return None

    ordered = sorted(dated.items())
    latest_date, latest_value = ordered[-1]
    meta = SERIES.get(
        series_id,
        {"label": series_id, "unit": "index", "frequency": "monthly", "note": ""},
    )
    target_date = _anniversary(latest_date)
    prior_candidates = ordered[:-1]
    prior = (
        min(prior_candidates, key=lambda item: abs((item[0] - target_date).days))
        if prior_candidates
        else None
    )
    # Stay inside half of the observation interval. If the actual comparable month/quarter is
    # absent, an adjacent period is not silently relabeled as year-over-year.
    tolerance_days = {"daily": 7, "monthly": 20, "quarterly": 50}.get(
        meta.get("frequency", "monthly"), 20
    )
    yoy = None
    if prior and abs((prior[0] - target_date).days) <= tolerance_days and prior[1] != 0:
        yoy = round((latest_value - prior[1]) / abs(prior[1]), 4)

    cutoff = latest_date - timedelta(days=366 * 5)
    compact_points = [
        {"date": observation_date.isoformat(), "value": value}
        for observation_date, value in ordered
        if observation_date >= cutoff
    ]
    return {
        "series_id": series_id,
        "label": meta["label"],
        "unit": meta["unit"],
        "note": meta["note"],
        "latest_value": latest_value,
        "latest_date": latest_date.isoformat(),
        "yoy_change": yoy,
        "points": compact_points,
    }


def macro_for_sector(sector: str) -> list[dict]:
    """Fetch the macro series most relevant to a company's sector (skips any that fail)."""
    ids = sectors_series(sector)
    return [s for s in (_fetch_series(i) for i in ids) if s]


def commentary(series: list[dict]) -> str:
    """One-line macro read from the latest values (deterministic)."""
    bits = []
    for s in series:
        v = s["latest_value"]
        if s["unit"] == "pct":
            bits.append(f"{s['label']} {v:.2f}%")
        elif s["yoy_change"] is not None:
            bits.append(f"{s['label']} {s['yoy_change']*100:+.1f}% YoY")
        else:
            bits.append(f"{s['label']} {v:,.0f}")
    return "Current macro backdrop: " + "; ".join(bits) + "." if bits else "No macro series available."
