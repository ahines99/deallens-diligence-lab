"""Quality-of-Earnings + financial forensics — deterministic, auditable, computed on GET.

All inputs come from `target.financials["forensic_inputs"]` (per-year XBRL fields already extracted at
ingestion) plus headline `target` fields. We NEVER re-fetch XBRL and NEVER impute a missing field:
when a required input is `None`, the affected score/metric degrades to `available=False` / `value=None`.

Scores (each a ForensicScore): Altman Z'' (private), Piotroski F (0-9), Beneish M (shown as an
unscored reduced value when DEPI is unavailable), Sloan accruals ratio. QoE metrics (each a
QoEMetric): net working capital, DSO/DIO/DPO,
cash-conversion cycle, FCF, cash conversion, interest coverage, EBITDA, net debt, net-debt/EBITDA leverage.

`risk_flags(session, workspace_id)` re-uses the same math to emit red-flag finding dicts (same shape as
`RiskAnalyst.financial_flags`) for the integration agent to splice into `analysis_service`.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.services.common import NotFound
from src.services.workspace_service import get_target

# --- tiny numeric helpers (all None-safe; never impute) --------------------


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pos(x: float | None) -> bool | None:
    return None if x is None else x > 0


def _gt(a: float | None, b: float | None) -> bool | None:
    if a is None or b is None:
        return None
    return a > b


def _r(x: float | None, n: int) -> float | None:
    return None if x is None else round(x, n)


def _gross_margin(row: dict) -> float | None:
    """Gross margin from gross_profit/revenue, falling back to (revenue - cogs)/revenue."""
    gp = row.get("gross_profit")
    rev = row.get("revenue")
    if gp is not None and rev not in (None, 0):
        return gp / rev
    cogs = row.get("cogs")
    if rev not in (None, 0) and cogs is not None:
        return (rev - cogs) / rev
    return None


def _ebit(row: dict) -> float | None:
    """EBIT = operating_income, fallback net_income + tax + interest (only if all three tagged)."""
    oi = row.get("operating_income")
    if oi is not None:
        return oi
    ni, tax, interest = row.get("net_income"), row.get("tax"), row.get("interest")
    if ni is not None and tax is not None and interest is not None:
        return ni + tax + interest
    return None


def _debt_sum(row: dict) -> float | None:
    """Interest-bearing debt from a complete tagged component set.

    An untagged tranche is unknown, not zero. Returning ``None`` prevents a partial debt subtotal
    from being presented as total debt or used in net-leverage calculations.
    """
    parts = [row.get("ltd"), row.get("ltd_current"), row.get("short_debt")]
    if any(part is None for part in parts):
        return None
    return sum(parts)


# --- forensic scores -------------------------------------------------------


def _comp(name: str, value: float | None) -> dict:
    return {"name": name, "value": value}


def _altman(t: dict) -> dict:
    """Altman Z'' (private/non-manufacturing): 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4."""
    assets = t.get("assets")
    ca, cl = t.get("current_assets"), t.get("current_liabilities")
    x1 = _div(ca - cl, assets) if ca is not None and cl is not None else None
    x2 = _div(t.get("retained_earnings"), assets)
    x3 = _div(_ebit(t), assets)
    x4 = _div(t.get("equity"), t.get("total_liabilities"))
    components = [_comp("X1 working capital / assets", _r(x1, 4)),
                 _comp("X2 retained earnings / assets", _r(x2, 4)),
                 _comp("X3 EBIT / assets", _r(x3, 4)),
                 _comp("X4 equity / total liabilities", _r(x4, 4))]
    if None in (x1, x2, x3, x4):
        return {"key": "altman_z", "label": "Altman Z'' (distress)", "value": None,
                "rating": "n/a", "available": False, "components": components,
                "interpretation": "Insufficient balance-sheet tags to compute Altman Z''.", "note": None}
    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
    if z > 2.6:
        rating, band = "strong", "safe zone (>2.6)"
    elif z >= 1.1:
        rating, band = "neutral", "grey zone (1.1-2.6)"
    else:
        rating, band = "distress", "distress zone (<1.1)"
    return {"key": "altman_z", "label": "Altman Z'' (distress)", "value": _r(z, 3),
            "rating": rating, "available": True, "components": components,
            "interpretation": f"Z''={z:.2f} — {band}. Higher is safer for a private/service-firm balance sheet.",
            "note": None}


def _piotroski(t: dict, p: dict | None) -> dict:
    """Piotroski F-score (0-9). Nine binary fundamental signals, needs t and t-1."""
    if p is None:
        return {"key": "piotroski_f", "label": "Piotroski F-score", "value": None, "rating": "n/a",
                "available": False, "components": [],
                "interpretation": "Needs a prior fiscal year; only one year of data available.", "note": None}
    roa_t, roa_p = _div(t.get("net_income"), t.get("assets")), _div(p.get("net_income"), p.get("assets"))
    lev_t, lev_p = _div(t.get("ltd"), t.get("assets")), _div(p.get("ltd"), p.get("assets"))
    cr_t = _div(t.get("current_assets"), t.get("current_liabilities"))
    cr_p = _div(p.get("current_assets"), p.get("current_liabilities"))
    at_t, at_p = _div(t.get("revenue"), t.get("assets")), _div(p.get("revenue"), p.get("assets"))
    sh_t, sh_p = t.get("shares_out"), p.get("shares_out")
    no_dilution = None if sh_t is None or sh_p is None else sh_t <= sh_p
    signals = [
        ("Positive ROA", _pos(roa_t)),
        ("Positive operating cash flow", _pos(t.get("cfo"))),
        ("Rising ROA", _gt(roa_t, roa_p)),
        ("CFO > net income (accrual quality)", _gt(t.get("cfo"), t.get("net_income"))),
        ("Falling leverage (LTD/assets)", _gt(lev_p, lev_t)),
        ("Rising current ratio", _gt(cr_t, cr_p)),
        ("No share dilution", no_dilution),
        ("Rising gross margin", _gt(_gross_margin(t), _gross_margin(p))),
        ("Rising asset turnover", _gt(at_t, at_p)),
    ]
    components = [_comp(n, None if v is None else float(bool(v))) for n, v in signals]
    unscored = [name for name, value in signals if value is None]
    if unscored:
        return {
            "key": "piotroski_f",
            "label": "Piotroski F-score",
            "value": None,
            "rating": "n/a",
            "available": False,
            "components": components,
            "interpretation": (
                "Insufficient tags for a comparable 0-9 Piotroski score; missing signals are "
                "unscored rather than counted as failures."
            ),
            "note": "Unscored: " + ", ".join(unscored) + ".",
        }
    score = sum(1 for _, v in signals if v is True)
    if score <= 2:
        rating = "weak"
    elif score >= 7:
        rating = "strong"
    else:
        rating = "neutral"
    return {"key": "piotroski_f", "label": "Piotroski F-score", "value": float(score), "rating": rating,
            "available": True, "components": components,
            "interpretation": f"F={score}/9 — higher signals stronger fundamentals (profitability, "
                              f"leverage, efficiency). F<=2 is a red flag; F>=8 is a positive.", "note": None}


def _beneish(t: dict, p: dict | None) -> dict:
    """Beneish M-score; a missing DEPI produces a reduced display value, never a threshold score."""
    if p is None:
        return {"key": "beneish_m", "label": "Beneish M-score", "value": None, "rating": "n/a",
                "available": False, "components": [],
                "interpretation": "Needs a prior fiscal year; only one year of data available.", "note": None}
    dsri = _div(_div(t.get("receivables"), t.get("revenue")), _div(p.get("receivables"), p.get("revenue")))
    gmi = _div(_gross_margin(p), _gross_margin(t))
    aqi = _div(_aqi_ratio(t), _aqi_ratio(p))
    sgi = _div(t.get("revenue"), p.get("revenue"))
    sgai = _div(_div(t.get("sga"), t.get("revenue")), _div(p.get("sga"), p.get("revenue")))
    lvg_t = _div(_lvg_debt(t), t.get("assets"))
    lvg_p = _div(_lvg_debt(p), p.get("assets"))
    lvgi = _div(lvg_t, lvg_p)
    tata = _div((t.get("net_income") - t.get("cfo")) if t.get("net_income") is not None
                and t.get("cfo") is not None else None, t.get("assets"))
    # DEPI (suppress if D&A None in either year).
    depi = None
    da_t, da_p, ppe_t, ppe_p = t.get("da"), p.get("da"), t.get("ppe_net"), p.get("ppe_net")
    depi_suppressed = None in (da_t, da_p, ppe_t, ppe_p)
    if not depi_suppressed:
        depi = _div(_div(da_p, (da_p or 0) + (ppe_p or 0)), _div(da_t, (da_t or 0) + (ppe_t or 0)))

    required = [dsri, gmi, aqi, sgi, sgai, lvgi, tata]
    components = [_comp("DSRI days-sales-receivable", _r(dsri, 4)),
                 _comp("GMI gross-margin", _r(gmi, 4)),
                 _comp("AQI asset-quality", _r(aqi, 4)),
                 _comp("SGI sales-growth", _r(sgi, 4)),
                 _comp("DEPI depreciation", _r(depi, 4)),
                 _comp("SGAI SG&A", _r(sgai, 4)),
                 _comp("LVGI leverage", _r(lvgi, 4)),
                 _comp("TATA total-accruals/assets", _r(tata, 4))]
    if None in required or (not depi_suppressed and depi is None):
        return {"key": "beneish_m", "label": "Beneish M-score", "value": None, "rating": "n/a",
                "available": False, "components": components,
                "interpretation": "Insufficient tags to compute the Beneish M-score.", "note": None}
    m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    if not depi_suppressed:
        m += 0.115 * depi
    if depi_suppressed:
        note = (
            "DEPI omitted (D&A untagged)"
            if da_t is None or da_p is None
            else "DEPI omitted (PP&E untagged)"
        )
        rating = "unscored"
        interp = (
            f"Reduced seven-variable value={m:.2f}. It is not comparable to the standard -1.78 "
            "eight-variable Beneish threshold and is not used as a red flag."
        )
    else:
        note = None
        rating = "elevated" if m > -1.78 else "neutral"
        interp = (f"M={m:.2f}. Above -1.78 flags elevated earnings-manipulation likelihood; "
                  f"below is consistent with non-manipulators.")
    return {"key": "beneish_m", "label": "Beneish M-score", "value": _r(m, 3), "rating": rating,
            "available": True, "components": components, "interpretation": interp, "note": note}


def _aqi_ratio(row: dict) -> float | None:
    """Beneish AQI component = 1 - (current_assets + ppe_net) / assets."""
    ca, ppe = row.get("current_assets"), row.get("ppe_net")
    if ca is None or ppe is None:
        return None
    frac = _div(ca + ppe, row.get("assets"))
    return None if frac is None else 1 - frac


def _lvg_debt(row: dict) -> float | None:
    """Beneish LVGI numerator = current liabilities + long-term debt.

    Current liabilities already include current debt maturities, so ``ltd_current`` must not be
    added again. Missing long-term debt is unknown—not an assumed zero.
    """
    cl, ltd = row.get("current_liabilities"), row.get("ltd")
    if cl is None or ltd is None:
        return None
    return cl + ltd


def _accruals(t: dict) -> dict:
    """Sloan accruals ratio = (net_income - cfo) / assets. High positive => lower earnings quality."""
    ni, cfo, assets = t.get("net_income"), t.get("cfo"), t.get("assets")
    val = _div(ni - cfo, assets) if ni is not None and cfo is not None else None
    components = [_comp("net_income", ni), _comp("cfo", cfo), _comp("assets", assets)]
    if val is None:
        return {"key": "accruals", "label": "Accruals ratio (Sloan)", "value": None, "rating": "n/a",
                "available": False, "components": components,
                "interpretation": "Needs net income, operating cash flow and assets.", "note": None}
    if val > 0.10:
        rating = "weak"
    elif val <= 0.03:
        rating = "strong"
    else:
        rating = "neutral"
    return {"key": "accruals", "label": "Accruals ratio (Sloan)", "value": _r(val, 4), "rating": rating,
            "available": True, "components": components,
            "interpretation": f"Accruals/assets={val:.1%}. Lower (or negative, i.e. cash exceeds earnings) "
                              f"indicates higher earnings quality; high positive accruals are a caution.",
            "note": None}


# --- QoE metrics -----------------------------------------------------------


def _average(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None:
        return None
    return (current + prior) / 2


def _qoe(t: dict, p: dict | None) -> list[dict]:
    rev, cogs = t.get("revenue"), t.get("cogs")
    ca, cl = t.get("current_assets"), t.get("current_liabilities")
    nwc = ca - cl if ca is not None and cl is not None else None
    prior = p or {}
    dso = _mul(_div(_average(t.get("receivables"), prior.get("receivables")), rev), 365)
    dio = _mul(_div(_average(t.get("inventory"), prior.get("inventory")), cogs), 365)
    dpo = _mul(_div(_average(t.get("payables"), prior.get("payables")), cogs), 365)
    ccc = dso + dio - dpo if None not in (dso, dio, dpo) else None
    cfo, capex = t.get("cfo"), t.get("capex")
    fcf = cfo - capex if cfo is not None and capex is not None else None
    cash_conv = _div(cfo, t.get("net_income"))
    ebit, interest = _ebit(t), t.get("interest")
    int_cov = _div(ebit, interest)
    da = t.get("da")
    ebitda = (t.get("operating_income") + da) if t.get("operating_income") is not None and da is not None else None
    cash = t.get("cash")
    debt = _debt_sum(t)
    net_debt = debt - cash if debt is not None and cash is not None else None
    leverage = _div(net_debt, ebitda)

    return [
        _m("net_working_capital", "Net working capital", "usd", _r(nwc, 0),
           "Current assets less current liabilities — the operating capital tied up in the business."),
        _m("dso", "Days sales outstanding (DSO)", "days", _r(dso, 1),
           "Average opening/closing receivables divided by revenue; rising DSO can flag "
           "revenue-recognition or collection stress."),
        _m("dio", "Days inventory outstanding (DIO)", "days", _r(dio, 1),
           "Average opening/closing inventory divided by COGS; n/a without both balance dates."),
        _m("dpo", "Days payable outstanding (DPO)", "days", _r(dpo, 1),
           "Average opening/closing payables divided by COGS; higher DPO conserves cash."),
        _m("cash_conversion_cycle", "Cash conversion cycle", "days", _r(ccc, 1),
           "DSO + DIO - DPO. Lower (or negative) is a stronger working-capital profile."),
        _m("fcf", "Free cash flow", "usd", _r(fcf, 0),
           "Operating cash flow less capex — cash available to service debt and equity."),
        _m("cash_conversion", "Cash conversion (CFO / net income)", "x", _r(cash_conv, 2),
           "Ratio of operating cash flow to GAAP net income; near or above 1.0x supports earnings quality."),
        _m("interest_coverage", "Interest coverage (EBIT / interest)", "x", _r(int_cov, 2),
           "EBIT divided by interest expense; n/a when interest expense is untagged."),
        _m("ebitda", "EBITDA", "usd", _r(ebitda, 0),
           "Operating income + D&A. n/a (EBIT-only) when D&A is untagged in XBRL."),
        _m("net_debt", "Net debt", "usd", _r(net_debt, 0),
           "Fully tagged long-term, current-maturity and short-term debt less same-period cash; "
           "n/a if any component is untagged."),
        _m("leverage_nd_ebitda", "Net debt / EBITDA", "x", _r(leverage, 2),
           "Turns of leverage; n/a when EBITDA is unavailable (D&A untagged)."),
    ]


def _mul(x: float | None, k: float) -> float | None:
    return None if x is None else x * k


def _m(key: str, label: str, unit: str, value: float | None, commentary: str) -> dict:
    if value is None:
        commentary = commentary + " (n/a — required input untagged in XBRL)."
    return {"key": key, "label": label, "unit": unit, "value": value, "commentary": commentary}


# --- fiscal-period consistency diagnostics (G17 / ledger F41) ---------------
# Operand end dates within this many days count as the same reporting period (52/53-week drift).
_PERIOD_TOLERANCE_DAYS = 7

# Derived headline metric -> the (numerator, denominator) source points it was computed from.
_DERIVED_METRIC_OPERANDS = {
    "gross_margin": ("gross_profit", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "net_margin": ("net_income", "revenue"),
    "rnd_pct": ("rnd", "revenue"),
}
# Balance-sheet instants that must be dated at the income-statement period end.
_BALANCE_ALIGNED = ("cash", "total_debt")


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def _period_label(point: dict) -> str:
    fy = str(point.get("fy") or "").strip()
    end = point.get("end") or "unknown end date"
    return f"FY{fy} (ending {end})" if fy else f"period ending {end}"


def _periods_differ(a: dict, b: dict) -> bool:
    # End dates are authoritative: XBRL `fy` is the fiscal year of the *filing* that reported a
    # fact, so a comparative instant retained from a newer 10-K can carry fy=N+1 while sharing an
    # identical period end with an fy=N operand. Judge on dates first; the fy label is only a
    # fallback signal when a date is missing, never an override of matching dates.
    end_a, end_b = _iso(a.get("end")), _iso(b.get("end"))
    if end_a is not None and end_b is not None:
        return abs((end_a - end_b).days) > _PERIOD_TOLERANCE_DAYS
    fy_a, fy_b = str(a.get("fy") or "").strip(), str(b.get("fy") or "").strip()
    if fy_a and fy_b and fy_a != fy_b:
        return True
    return False  # not judgeable without both dates and no conflicting fy labels


def _diagnostic(metric: str, a: dict, b: dict, detail: str, severity: str = "high") -> dict:
    return {
        "metric": metric,
        "period_a": _period_label(a),
        "period_b": _period_label(b),
        "severity": severity,
        "detail": detail,
    }


def fiscal_diagnostics(financials: dict | None) -> list[dict] | None:
    """Verify every multi-operand derived metric used operands from the same reporting period.

    Checks the stored headline metrics (margins, R&D %, and the balance-sheet instants behind net
    cash/debt) against their per-metric XBRL source points: operand end dates must agree within
    ``_PERIOD_TOLERANCE_DAYS`` and fiscal-year labels must match when both are present. The
    Rule-of-40 growth leg intentionally spans two consecutive fiscal years and its margin leg is
    covered by the operating-margin check. Forensic composites consume single-year rows keyed by
    fiscal label at extraction (`forensic_inputs.by_year`), so cross-period blending cannot occur
    there by construction.

    Returns [] when consistent, mismatch diagnostics ({metric, period_a, period_b, severity,
    detail}) otherwise, or None when the stored financials carry no source points (legacy
    workspace — not computable). Purely diagnostic: numeric outputs are never modified.
    """
    if not financials or not isinstance(financials.get("sources"), dict):
        return None
    sources = financials["sources"]
    diagnostics: list[dict] = []
    for metric, (num_key, den_key) in _DERIVED_METRIC_OPERANDS.items():
        if financials.get(metric) is None:
            continue  # nothing was derived, so nothing could have been blended
        num, den = sources.get(num_key), sources.get(den_key)
        if not num or not den:
            continue
        if _periods_differ(num, den):
            diagnostics.append(_diagnostic(
                metric, num, den,
                f"{metric} was derived from {num_key} for {_period_label(num)} and {den_key} "
                f"for {_period_label(den)}; operands must share one reporting period.",
            ))
    revenue = sources.get("revenue")
    if revenue:
        for key in _BALANCE_ALIGNED:
            point = sources.get(key)
            if not point or financials.get(key) is None:
                continue
            if _periods_differ(point, revenue):
                diagnostics.append(_diagnostic(
                    key, point, revenue,
                    f"balance-sheet {key} is dated {_period_label(point)} but the reporting "
                    f"period is {_period_label(revenue)}; instants must match the period end.",
                    severity="medium",
                ))
    return diagnostics


# --- assembly --------------------------------------------------------------


def _rows(forensic_inputs: dict) -> tuple[str | None, dict, dict | None]:
    years = forensic_inputs.get("years") or []
    by_year = forensic_inputs.get("by_year") or {}
    if not years or not by_year:
        raise NotFound("No forensic inputs available; ingest a company with XBRL financials first.")
    t_year = years[-1]
    t = by_year.get(t_year) or {}
    p = by_year.get(years[-2]) if len(years) >= 2 else None
    return t_year, t, p


def _core(forensic_inputs: dict, target) -> dict:
    t_year, t, p = _rows(forensic_inputs)
    scores = [_altman(t), _piotroski(t, p), _beneish(t, p), _accruals(t)]
    qoe = _qoe(t, p)
    notes = [
        "All figures are computed deterministically from SEC XBRL company facts stored at ingestion; "
        "no values are imputed — missing tags degrade to n/a.",
        "Altman Z'' uses the private/non-manufacturer coefficients; Piotroski and Beneish require the "
        "prior fiscal year and are n/a with a single year of data.",
    ]
    if p is None:
        notes.append("Only one fiscal year is available, so year-over-year scores (Piotroski, Beneish) are n/a.")
    if any(s["key"] == "beneish_m" and s.get("note") for s in scores):
        notes.append(
            "A reduced Beneish value is shown without DEPI, but it is unscored and not compared "
            "with the standard eight-variable manipulation threshold."
        )
    return {"as_of_year": t_year, "scores": scores, "qoe": qoe, "notes": notes}


def compute_forensics(session: Session, workspace_id: str) -> dict:
    target = get_target(session, workspace_id)
    if target is None or not (target.financials or {}).get("forensic_inputs"):
        raise NotFound("No forensic inputs for this workspace; ingest a company with XBRL financials first.")
    core = _core(target.financials["forensic_inputs"], target)
    return {
        "workspace_id": workspace_id,
        "target_name": target.name,
        "as_of_year": core["as_of_year"],
        "scores": core["scores"],
        "qoe": core["qoe"],
        "notes": core["notes"],
        # [] = every derived metric used same-period operands; None = legacy workspace without
        # stored source points (not computable). Additive: never changes any numeric output.
        "fiscal_diagnostics": fiscal_diagnostics(target.financials),
        "generated_at": now_utc(),
    }


# --- risk flags (spliced into analysis_service by the integration agent) ----


def _score(scores: list[dict], key: str) -> dict | None:
    return next((s for s in scores if s["key"] == key), None)


def _evidence(target, year: str | None, claim: str, text: str, concepts: str, conf: float) -> dict:
    return {
        "claim": claim,
        "claim_type": "calculation",
        "evidence_text": text,
        "source_name": f"{target.name} FY{year or 'latest'} forensic model (SEC XBRL)",
        "source_type": "xbrl",
        "source_url": None,
        "source_date": target.fiscal_year_end,
        "source_section": f"XBRL company facts — {concepts}",
        "confidence": conf,
        "agent_name": "forensics_analyst",
    }


def _finding(cat, label, title, finding, severity, score, conf, followup, evidence) -> dict:
    return {
        "risk_category": cat,
        "risk_category_label": label,
        "title": title,
        "finding": finding,
        "severity": severity,
        "severity_score": score,
        "likelihood": "high" if score >= 6 else "medium",
        "confidence": conf,
        "workstream_owner": "financial",
        "follow_up_question": followup,
        "evidence": evidence,
    }


# A near-term "maturity wall": at least this share of the fully-tagged maturity schedule falls due
# within the next two fiscal years (Y1 + Y2).
_NEAR_TERM_WALL_THRESHOLD = 0.5


def _append_maturity_wall_flag(flags: list[dict], target, year: str | None) -> None:
    """Flag a near-term debt maturity wall when Y1+Y2 dominate a COMPLETE maturity schedule.

    Only fires on ``status == "available"`` (every bucket tagged) so the denominator is the full
    schedule — never a partial one that would overstate the near-term share. Additive: leaves all
    existing flags untouched.
    """
    maturities = (target.financials or {}).get("debt_maturities")
    if not maturities or maturities.get("status") != "available":
        return
    total = maturities.get("total_scheduled")
    if not total or total <= 0:
        return
    by_bucket = {row["bucket"]: row["amount"] for row in maturities.get("schedule", [])}
    near_term = by_bucket.get("Y1", 0.0) + by_bucket.get("Y2", 0.0)
    share = near_term / total
    if share < _NEAR_TERM_WALL_THRESHOLD:
        return
    as_of = maturities.get("as_of")
    flags.append(_finding(
        "debt_liquidity", "Debt & liquidity",
        "Near-term debt maturity wall",
        f"{target.name} has {share:.0%} of its scheduled long-term-debt principal "
        f"({near_term:,.0f} of {total:,.0f}) maturing within two fiscal years (as of {as_of}). "
        "A concentrated near-term maturity wall raises refinancing risk, especially in a higher-rate "
        "environment; the repayment or refinancing plan warrants scrutiny.",
        "high", 6, 0.8,
        "How is the near-term maturity wall funded — refinancing commitments, cash on hand, or free "
        "cash flow — and at what expected cost?",
        _evidence(target, year, f"{share:.0%} of scheduled principal matures within two years.",
                  f"Y1+Y2 maturities = {near_term:,.0f} of {total:,.0f} scheduled principal "
                  f"(as of {as_of}).",
                  "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths / ...InYearTwo",
                  0.8),
    ))


def risk_flags(session: Session, workspace_id: str) -> list[dict]:
    """Deterministic forensic red flags in the RiskAnalyst.financial_flags shape.

    Altman Z''<1.1 -> debt_liquidity high(7); Beneish M>-1.78 -> financial medium(6);
    Piotroski F<=2 -> financial medium(6). Returns [] when there is nothing to score.
    """
    target = get_target(session, workspace_id)
    if target is None or not (target.financials or {}).get("forensic_inputs"):
        return []
    try:
        core = _core(target.financials["forensic_inputs"], target)
    except NotFound:
        return []
    year = core["as_of_year"]
    scores = core["scores"]
    flags: list[dict] = []

    altman = _score(scores, "altman_z")
    if altman and altman["available"] and altman["value"] is not None and altman["value"] < 1.1:
        flags.append(_finding(
            "debt_liquidity", "Debt & liquidity",
            "Altman Z'' in the distress zone",
            f"{target.name}'s Altman Z''-score is {altman['value']:.2f} (FY{year}), inside the <1.1 distress "
            f"zone. The private-firm Z'' blends working capital, retained earnings, EBIT and equity/leverage; "
            f"a distress-zone reading warrants scrutiny of solvency and refinancing risk.",
            "high", 7, 0.85,
            "What supports going-concern given the distress-zone Z''? Review liquidity, covenants and maturities.",
            _evidence(target, year, f"Altman Z'' = {altman['value']:.2f} (distress zone).",
                      "Z''=6.56·X1+3.26·X2+6.72·X3+1.05·X4 = "
                      f"{altman['value']:.2f}; <1.1 = distress.",
                      "Assets, LiabilitiesCurrent, RetainedEarnings, OperatingIncomeLoss, StockholdersEquity",
                      0.85),
        ))

    beneish = _score(scores, "beneish_m")
    if (beneish and beneish["available"] and beneish["value"] is not None
            and not beneish.get("note") and beneish["value"] > -1.78):
        flags.append(_finding(
            "margin_pressure", "Earnings quality",
            "Beneish M-score signals elevated manipulation likelihood",
            f"{target.name}'s Beneish M-score is {beneish['value']:.2f} (FY{year}), above the -1.78 threshold — "
            f"elevated earnings-manipulation likelihood. This is a statistical screen, not proof; it flags "
            "accrual, receivable and margin dynamics for closer review.",
            "medium", 6, 0.75,
            "Reconcile revenue recognition, receivables growth and accruals against cash collections and disclosures.",
            _evidence(target, year, f"Beneish M-score = {beneish['value']:.2f} (> -1.78).",
                      f"M = -4.84 + weighted DSRI/GMI/AQI/SGI/SGAI/LVGI/TATA = {beneish['value']:.2f}.",
                      "AccountsReceivableNetCurrent, Revenues, GrossProfit, Assets, NetIncomeLoss, CFO",
                      0.75),
        ))

    piotroski = _score(scores, "piotroski_f")
    if piotroski and piotroski["available"] and piotroski["value"] is not None and piotroski["value"] <= 2:
        flags.append(_finding(
            "debt_liquidity", "Financial strength",
            "Weak Piotroski F-score",
            f"{target.name}'s Piotroski F-score is {int(piotroski['value'])}/9 (FY{year}), at or below 2 — weak "
            f"fundamental strength across profitability, leverage/liquidity and operating efficiency.",
            "medium", 6, 0.78,
            "Which of the failing Piotroski signals (profitability, leverage, efficiency) are structural vs. transient?",
            _evidence(target, year, f"Piotroski F-score = {int(piotroski['value'])}/9 (<=2).",
                      "Nine binary signals over profitability, leverage/liquidity and efficiency; "
                      f"score = {int(piotroski['value'])}.",
                      "NetIncomeLoss, CFO, Assets, LongTermDebt, LiabilitiesCurrent, Revenues, GrossProfit",
                      0.78),
        ))

    _append_maturity_wall_flag(flags, target, year)

    diagnostics = fiscal_diagnostics(target.financials)
    if diagnostics:
        listing = "; ".join(
            f"{d['metric']} ({d['period_a']} vs {d['period_b']})" for d in diagnostics
        )
        flags.append(_finding(
            "margin_pressure", "Reporting-period consistency",
            "Derived metrics mix fiscal reporting periods",
            f"{target.name} has {len(diagnostics)} stored derived metric(s) whose operands come from "
            f"different reporting periods: {listing}. Mixed-period ratios are unreliable and are "
            "flagged rather than silently blended; refresh the ingestion to recompute them from a "
            "single reporting period.",
            "medium", 5, 0.9,
            "Re-ingest the target so every derived ratio uses operands from the same fiscal period.",
            _evidence(target, year, "Fiscal-period consistency check flagged mixed-period operands.",
                      f"Mismatched operand periods: {listing}.",
                      "XBRL period end dates and fiscal-year labels per stored source point",
                      0.9),
        ))

    return flags
