"""G68 — Macro-linked Monte Carlo presets: a transparent, versioned FRED -> distribution map.

This module is a CONFIG, not a model. Every mapping is a documented linear function with its
coefficients exposed as module-level, reviewable constants; ``PRESET_VERSION`` stamps the
mapping generation on every payload. The user loads these presets client-side, EDITS them, and
only then runs Monte Carlo — the server supplies provenance, never a hidden calibration.

v1 mapping (all fetched through ``fred_service``'s keyless fredgraph endpoint; an unavailable
series yields NO distribution for that driver — a preset entry is never fabricated):

* ``DGS10`` (10-year Treasury yield, percent) -> ``base_rate_shift`` ~ normal(mean = last
  DGS10 / 100, std = sample std of the last ``BASE_RATE_WINDOW_OBS`` observations / 100).
  The engine ADDS the sampled value to the case's ``base_rate``, so this preset expresses the
  ABSOLUTE market rate: pair it with a case whose ``base_rate`` is 0, or subtract the case's
  modeled base rate from the mean before running (stated in ``mapping_notes``).
* ``BAA10Y`` (Moody's Baa corporate spread over the 10-year Treasury, percent) ->
  ``exit_multiple`` ~ normal(mean = ``EXIT_MULTIPLE_ANCHOR_TURNS`` +
  ``SPREAD_COEFFICIENT_TURNS_PER_PCT`` x (last spread - ``SPREAD_NEUTRAL_PCT``),
  std = |coefficient| x sample std of the last ``SPREAD_WINDOW_OBS`` observations). A wider
  credit spread lowers the mean exit multiple (the coefficient is negative). The engine samples
  ``exit_multiple`` as ABSOLUTE turns, and the anchor is a generic placeholder — replace it
  with the case's own exit multiple before running.
* ``A191RL1Q225SBEA`` (real GDP growth, percent change SAAR, quarterly) ->
  ``revenue_growth_shift`` ~ normal(mean = ``GDP_REVENUE_BETA`` x (last growth -
  ``GDP_TREND_PCT``) / 100, std = ``GDP_REVENUE_BETA`` x sample std of the last
  ``GDP_WINDOW_OBS`` observations / 100). Beta 1.0 is a neutral default — calibrate it to the
  target's cyclicality client-side.

Provenance design decision: ``MonteCarloRequest`` and the engine are UNCHANGED. Each
distribution dict carries a ``provenance`` field (series, as-of, window, formula) that the
Pydantic schema ignores on validation; the CALLER keeps it client-side and attaches it to the
MC result presentation, so preset-driven runs show their macro provenance with no engine change.
"""
from __future__ import annotations

import statistics

from src.db.base import now_utc
from src.services import fred_service
from src.services.common import get_workspace_or_404

PRESET_VERSION = "v1"

# --- Reviewable mapping constants (documented in the module docstring) ------------------------
# Observation windows for the historical sample std (fred_service serves a compact 5y window;
# 250 daily observations ~ one trading year, 20 quarterly observations = five years).
BASE_RATE_WINDOW_OBS = 250
SPREAD_WINDOW_OBS = 250
GDP_WINDOW_OBS = 20

# Spread -> exit multiple linear map. The anchor is a PLACEHOLDER mid-market EV/EBITDA level the
# user must replace with the case's exit multiple; the coefficient encodes "wider credit spread
# -> lower exit multiple" at -1.0 turns per +1.00% of Baa-10Y spread above neutral.
EXIT_MULTIPLE_ANCHOR_TURNS = 10.0
SPREAD_NEUTRAL_PCT = 2.0
SPREAD_COEFFICIENT_TURNS_PER_PCT = -1.0

# GDP growth -> revenue growth shift: beta 1.0 around a 2.0% long-run trend.
GDP_TREND_PCT = 2.0
GDP_REVENUE_BETA = 1.0

_SERIES_LABELS = {
    "DGS10": "10-year Treasury yield",
    "BAA10Y": "Moody's Baa spread over 10-year Treasury",
    "A191RL1Q225SBEA": "Real GDP growth (percent change, SAAR)",
}

_UNAVAILABLE_NOTE = (
    "FRED series unavailable — this preset entry is omitted, never fabricated."
)
_THIN_HISTORY_NOTE = (
    "FRED series has fewer than 2 observations — no historical std can be computed, so this "
    "preset entry is omitted, never fabricated."
)


def _omit_note(summary: dict | None) -> str:
    return _UNAVAILABLE_NOTE if summary is None else _THIN_HISTORY_NOTE


def _window_std(points: list[dict], window: int) -> float | None:
    """Sample standard deviation of the last ``window`` observations, or None if under 2."""
    values = [float(point["value"]) for point in points[-window:]]
    if len(values) < 2:
        return None
    return statistics.stdev(values)


def _series_row(series_id: str, summary: dict | None, note: str | None = None) -> dict:
    return {
        "series_id": series_id,
        "label": _SERIES_LABELS[series_id],
        "last_value": summary["latest_value"] if summary else None,
        "as_of": summary["latest_date"] if summary else None,
        "note": note,
    }


def _fetch(series_id: str) -> dict | None:
    return fred_service._fetch_series(series_id)  # noqa: SLF001 — internal reuse, keyless fredgraph fetch


def _provenance(series_id: str, summary: dict, window: int, formula: str) -> dict:
    return {
        "preset_version": PRESET_VERSION,
        "series_id": series_id,
        "series_label": _SERIES_LABELS[series_id],
        "last_value": summary["latest_value"],
        "as_of": summary["latest_date"],
        "window_obs": window,
        "mapping": formula,
    }


def build(session=None, workspace_id: str | None = None) -> dict:
    """Generate the macro-linked Monte Carlo presets (transparent v1 mapping, user-editable).

    ``workspace_id`` is optional: presets are macro-level and workspace-independent; when a
    workspace is supplied it is validated and echoed for client routing. ``status`` is
    ``available`` when every series mapped, ``partial`` when some did, and ``unavailable`` when
    none did — a missing series never yields a fabricated distribution.
    """
    if session is not None and workspace_id is not None:
        get_workspace_or_404(session, workspace_id)

    series_rows: list[dict] = []
    distributions: list[dict] = []
    mapping_notes: list[str] = [
        f"Preset mapping {PRESET_VERSION} — every coefficient is a reviewable module constant "
        "in macro_preset_service; edit any distribution client-side before running Monte Carlo.",
        "Provenance rides on each distribution as a 'provenance' field the MC schema ignores; "
        "keep it client-side and attach it to the run's presentation (engine unchanged).",
    ]

    # DGS10 -> base_rate_shift (absolute market rate; see docstring for the pairing caveat).
    dgs10 = _fetch("DGS10")
    std = _window_std(dgs10["points"], BASE_RATE_WINDOW_OBS) if dgs10 else None
    if dgs10 and std is not None:
        mean = round(dgs10["latest_value"] / 100.0, 6)
        formula = (
            f"base_rate_shift ~ normal(mean = DGS10/100 = {mean}, std = stdev(last "
            f"{BASE_RATE_WINDOW_OBS} obs)/100 = {round(std / 100.0, 6)})"
        )
        distributions.append(
            {
                "driver": "base_rate_shift",
                "kind": "normal",
                "mean": mean,
                "std_dev": round(std / 100.0, 6),
                "provenance": _provenance("DGS10", dgs10, BASE_RATE_WINDOW_OBS, formula),
            }
        )
        series_rows.append(_series_row("DGS10", dgs10))
        mapping_notes.append(
            "base_rate_shift expresses the ABSOLUTE 10Y rate: the engine adds it to the case's "
            "base_rate, so set the case base_rate to 0 or subtract it from the mean."
        )
    else:
        series_rows.append(_series_row("DGS10", dgs10, _omit_note(dgs10)))

    # BAA10Y -> exit_multiple (wider spread -> lower mean multiple; anchor is a placeholder).
    spread = _fetch("BAA10Y")
    std = _window_std(spread["points"], SPREAD_WINDOW_OBS) if spread else None
    if spread and std is not None:
        mean = round(
            EXIT_MULTIPLE_ANCHOR_TURNS
            + SPREAD_COEFFICIENT_TURNS_PER_PCT * (spread["latest_value"] - SPREAD_NEUTRAL_PCT),
            6,
        )
        std_turns = round(abs(SPREAD_COEFFICIENT_TURNS_PER_PCT) * std, 6)
        formula = (
            f"exit_multiple ~ normal(mean = {EXIT_MULTIPLE_ANCHOR_TURNS} + "
            f"({SPREAD_COEFFICIENT_TURNS_PER_PCT}) x (BAA10Y - {SPREAD_NEUTRAL_PCT}) = {mean}, "
            f"std = |{SPREAD_COEFFICIENT_TURNS_PER_PCT}| x stdev(last {SPREAD_WINDOW_OBS} obs) "
            f"= {std_turns})"
        )
        distributions.append(
            {
                "driver": "exit_multiple",
                "kind": "normal",
                "mean": mean,
                "std_dev": std_turns,
                "provenance": _provenance("BAA10Y", spread, SPREAD_WINDOW_OBS, formula),
            }
        )
        series_rows.append(_series_row("BAA10Y", spread))
        mapping_notes.append(
            f"exit_multiple samples ABSOLUTE turns around a placeholder anchor of "
            f"{EXIT_MULTIPLE_ANCHOR_TURNS}x — replace the anchor with the case's exit multiple "
            "(keep the spread adjustment) before running."
        )
    else:
        series_rows.append(_series_row("BAA10Y", spread, _omit_note(spread)))

    # Real GDP growth -> revenue_growth_shift.
    gdp = _fetch("A191RL1Q225SBEA")
    std = _window_std(gdp["points"], GDP_WINDOW_OBS) if gdp else None
    if gdp and std is not None:
        mean = round(GDP_REVENUE_BETA * (gdp["latest_value"] - GDP_TREND_PCT) / 100.0, 6)
        std_shift = round(GDP_REVENUE_BETA * std / 100.0, 6)
        formula = (
            f"revenue_growth_shift ~ normal(mean = {GDP_REVENUE_BETA} x (GDP growth - "
            f"{GDP_TREND_PCT})/100 = {mean}, std = {GDP_REVENUE_BETA} x stdev(last "
            f"{GDP_WINDOW_OBS} obs)/100 = {std_shift})"
        )
        distributions.append(
            {
                "driver": "revenue_growth_shift",
                "kind": "normal",
                "mean": mean,
                "std_dev": std_shift,
                "provenance": _provenance("A191RL1Q225SBEA", gdp, GDP_WINDOW_OBS, formula),
            }
        )
        series_rows.append(_series_row("A191RL1Q225SBEA", gdp))
        mapping_notes.append(
            f"revenue_growth_shift uses a neutral beta of {GDP_REVENUE_BETA} around a "
            f"{GDP_TREND_PCT}% GDP trend — calibrate the beta to the target's cyclicality."
        )
    else:
        series_rows.append(_series_row("A191RL1Q225SBEA", gdp, _omit_note(gdp)))

    mapped = len(distributions)
    status = "available" if mapped == 3 else "partial" if mapped else "unavailable"
    if mapped < 3:
        mapping_notes.append(
            f"{3 - mapped} of 3 FRED series were unavailable; their preset entries are omitted "
            "rather than fabricated."
        )
    return {
        "workspace_id": workspace_id,
        "status": status,
        "preset_version": PRESET_VERSION,
        "generated_at": now_utc(),
        "series": series_rows,
        "distributions": distributions,
        "mapping_notes": mapping_notes,
    }
