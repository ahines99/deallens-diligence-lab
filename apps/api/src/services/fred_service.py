"""FRED macro overlay via the keyless fredgraph CSV endpoint (no API key required).

Provides a small set of macro series and maps a target's SEC sector to the most relevant ones,
so the diligence pack can note macro sensitivity with real, current data.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("deallens.fred")

FREDGRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# Curated macro series (id -> label, unit, "higher is" direction for commentary).
SERIES = {
    "FEDFUNDS": {"label": "Federal funds rate", "unit": "pct", "note": "Cost of capital / discount-rate pressure"},
    "DGS10": {"label": "10-year Treasury yield", "unit": "pct", "note": "Long-rate / valuation discount proxy"},
    "CPIAUCSL": {"label": "CPI (all urban)", "unit": "index", "note": "Inflation / input-cost pressure"},
    "UNRATE": {"label": "Unemployment rate", "unit": "pct", "note": "Labor market / demand proxy"},
    "INDPRO": {"label": "Industrial production", "unit": "index", "note": "Industrial/manufacturing demand"},
    "GDPC1": {"label": "Real GDP", "unit": "index", "note": "Broad demand backdrop"},
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
    if not points:
        return None
    # Keep the last 60 monthly-ish observations for a compact 5-year view.
    points = points[-60:]
    meta = SERIES.get(series_id, {"label": series_id, "unit": "index", "note": ""})
    latest = points[-1]
    prior_12 = points[-13] if len(points) >= 13 else points[0]
    yoy = None
    if prior_12["value"]:
        yoy = round((latest["value"] - prior_12["value"]) / abs(prior_12["value"]), 4)
    return {
        "series_id": series_id,
        "label": meta["label"],
        "unit": meta["unit"],
        "note": meta["note"],
        "latest_value": latest["value"],
        "latest_date": latest["date"],
        "yoy_change": yoy,
        "points": points,
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
