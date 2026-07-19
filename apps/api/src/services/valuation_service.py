"""Valuation & returns: an accounting-derived FCFF DCF and a levered-buyout returns model.

All figures are computed deterministically from data already stored at ingestion
(`target.financials["forensic_inputs"]` + headline target fields) plus the live risk-free rate
from FRED (DGS10, keyless). Nothing is re-fetched from XBRL. Every field degrades to `None`/"n/a"
when an input is missing — we never impute. Every assumption is labeled.

Formulas per BUILD_SPEC:
- WACC: risk_free = latest FRED DGS10 / 100; equity_risk_premium=0.05; beta=1.1;
  cost_of_equity = risk_free + beta*erp; cost_of_debt = risk_free + 0.02; tax=0.21;
  debt_weight = net_debt/(net_debt+equity); WACC = we*coe + wd*cod*(1-tax).
- FCFF DCF: FCFF proxy = CFO + interest expense * (1-tax) - capex (latest year), grown at
  `growth` (0.05) for 5 years, with a Gordon terminal value discounted at WACC -> EV.
- LBO: entry_ev=entry_multiple*EBITDA; entry_debt=leverage*EBITDA; entry_equity=entry_ev-entry_debt;
  project EBITDA at ebitda_cagr over hold_years; exit_ev=exit_multiple*exit_EBITDA;
  debt paid down by cumulative FCF proxy (fcf_conversion*EBITDA per year); exit_equity=exit_ev-exit_debt;
  MOIC=exit_equity/entry_equity; IRR=MOIC^(1/hold_years)-1. Sensitivity over entry x exit multiples.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.services import fred_service
from src.services.common import NotFound
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.valuation")

# --- Labeled assumptions (deterministic constants) --------------------------
EQUITY_RISK_PREMIUM = 0.05
BETA = 1.1
TAX_RATE = 0.21
COST_OF_DEBT_SPREAD = 0.02
DCF_GROWTH = 0.05
DCF_TERMINAL_GROWTH = 0.025
DCF_YEARS = 5
# Share of projected EBITDA assumed available to amortize acquisition debt each year
# (proxy for FCF after interest, cash taxes, and capex in an LBO).
LBO_FCF_CONVERSION = 0.5
SENSITIVITY_STEPS = 5
SENSITIVITY_DELTA = 2.0


def risk_free_rate() -> float | None:
    """Latest 10-year Treasury yield (FRED DGS10) as a decimal, or None if FRED is unreachable."""
    series = fred_service._fetch_series("DGS10")
    if not series or series.get("latest_value") is None:
        return None
    return round(series["latest_value"] / 100.0, 6)


# --- Input extraction from stored forensic inputs ---------------------------

def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _sum_complete(values: list) -> float | None:
    if any(not isinstance(value, (int, float)) for value in values):
        return None
    return sum(float(value) for value in values)


def _trusted_headline_debt(target) -> tuple[float | None, str | None]:
    """Return a directly reported debt value and an explicit coverage label."""

    source = ((target.financials or {}).get("sources") or {}).get("total_debt") or {}
    concept = source.get("concept")
    if concept == "DebtLongtermAndShorttermCombinedAmount":
        return _num(target.total_debt), "SEC combined long- and short-term debt"
    if concept == "LongTermDebt":
        return (
            _num(target.total_debt),
            "SEC reported long-term debt (including current maturities; separately reported "
            "short-term borrowing is not included)",
        )
    return None, None


def _fcff_base(
    cfo: float | None, capex: float | None, interest: float | None
) -> tuple[float | None, bool]:
    """Unlevered FCFF proxy = CFO + after-tax interest add-back - capex.

    CFO and capex are material and required — a missing one leaves FCFF (and the DCF) n/a; we
    never impute them. Interest expense is only an *adjustment*: CFO is already net of cash
    interest, and adding back after-tax interest approximates the unlevered figure. Some issuers
    (typically cash-rich ones) net interest into "other income (expense)" and never tag it on the
    face, so `interest` is absent. Rather than withhold the whole valuation for a small add-back,
    we omit it (treat it as 0) and return a flag so the omission is disclosed. The result,
    FCFF ~= CFO - capex, is a conservative approximation, not an imputed input.

    Returns (fcff_base, interest_addback_omitted).
    """
    if cfo is None or capex is None:
        return None, False
    interest_omitted = interest is None
    addback = 0.0 if interest is None else interest * (1.0 - TAX_RATE)
    return cfo + addback - capex, interest_omitted


def _core_inputs(target) -> dict:
    """Pull the latest-fiscal-year figures needed for valuation. Raises NotFound if unavailable."""
    fin = target.financials or {}
    fi = fin.get("forensic_inputs")
    if not fi or not fi.get("years") or not fi.get("by_year"):
        raise NotFound("No forensic inputs available; ingest a company with XBRL financials first.")
    years = fi["years"]
    t = years[-1]
    latest = fi["by_year"].get(t, {})

    operating_income = _num(latest.get("operating_income"))
    da = _num(latest.get("da"))
    ebitda = (operating_income + da) if (operating_income is not None and da is not None) else None

    # Prefer a directly reported aggregate. Otherwise require a complete component set. A missing
    # debt tranche is unknown, not zero.
    gross_debt, debt_basis = _trusted_headline_debt(target)
    if gross_debt is None:
        gross_debt = _sum_complete(
            [latest.get("ltd"), latest.get("ltd_current"), latest.get("short_debt")]
        )
        if gross_debt is not None:
            debt_basis = "complete tagged debt components"
    cash = _num(latest.get("cash"))
    net_debt = gross_debt - cash if gross_debt is not None and cash is not None else None

    equity = _num(latest.get("equity"))
    cfo = _num(latest.get("cfo"))
    capex = _num(latest.get("capex"))
    interest = _num(latest.get("interest"))
    fcff_base, interest_omitted = _fcff_base(cfo, capex, interest)

    return {
        "as_of_year": t,
        "ebitda": round(ebitda, 2) if ebitda is not None else None,
        "operating_income": operating_income,
        "da": da,
        "net_debt": round(net_debt, 2) if net_debt is not None else None,
        "net_debt_basis": debt_basis,
        "equity": equity,
        "fcf_base": round(fcff_base, 2) if fcff_base is not None else None,
        "fcf_interest_omitted": interest_omitted,
    }


# --- WACC / DCF (pure math on already-extracted inputs) ----------------------

def compute_wacc(risk_free: float | None, net_debt: float | None, equity: float | None) -> dict:
    """Assemble an illustrative WACC using the supplied capital values; missing -> None."""
    cost_of_equity = round(risk_free + BETA * EQUITY_RISK_PREMIUM, 6) if risk_free is not None else None
    cost_of_debt = round(risk_free + COST_OF_DEBT_SPREAD, 6) if risk_free is not None else None

    debt_weight = None
    if net_debt is not None and equity is not None:
        denom = net_debt + equity
        if denom > 0:
            # Clamp to [0,1]: a net-cash target reads as all-equity for WACC weighting.
            debt_weight = round(min(1.0, max(0.0, net_debt / denom)), 6)

    value = None
    if cost_of_equity is not None and cost_of_debt is not None and debt_weight is not None:
        equity_weight = 1.0 - debt_weight
        value = round(
            equity_weight * cost_of_equity + debt_weight * cost_of_debt * (1.0 - TAX_RATE), 6
        )

    return {
        "value": value,
        "risk_free": risk_free,
        "equity_risk_premium": EQUITY_RISK_PREMIUM,
        "beta": BETA,
        "cost_of_equity": cost_of_equity,
        "cost_of_debt": cost_of_debt,
        "tax_rate": TAX_RATE,
        "debt_weight": debt_weight,
    }


def compute_dcf(fcf_base: float | None, wacc: float | None,
                growth: float = DCF_GROWTH, terminal_growth: float = DCF_TERMINAL_GROWTH) -> dict:
    """Discount a 5-year growing FCFF stream plus Gordon terminal value to enterprise value."""
    assumptions = [
        (
            "Input is FCFF; the service-derived base equals CFO + after-tax interest expense "
            f"(when separately tagged; {TAX_RATE:.0%} tax assumption) - capex."
        ),
        f"FCFF grown {growth:.1%}/yr for {DCF_YEARS} years.",
        f"Gordon terminal value at {terminal_growth:.1%} perpetual growth.",
        "FCFF is discounted at WACC; pre-synergy, year-end convention.",
        "This is an illustrative accounting-data DCF, not a management forecast.",
    ]
    enterprise_value = None
    if (
        fcf_base is not None
        and fcf_base > 0
        and wacc is not None
        and wacc > terminal_growth  # Gordon model requires discount rate > terminal growth
    ):
        pv = 0.0
        fcf = fcf_base
        for year in range(1, DCF_YEARS + 1):
            fcf = fcf_base * (1.0 + growth) ** year
            pv += fcf / (1.0 + wacc) ** year
        terminal_value = fcf * (1.0 + terminal_growth) / (wacc - terminal_growth)
        pv += terminal_value / (1.0 + wacc) ** DCF_YEARS
        enterprise_value = round(pv, 2)
    return {
        "fcf_base": fcf_base,
        "growth": growth,
        "terminal_growth": terminal_growth,
        "wacc": wacc,
        "enterprise_value": enterprise_value,
        "assumptions": assumptions,
    }


def compute_valuation(session: Session, workspace_id: str) -> dict:
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set for this workspace; ingest a company first.")
    core = _core_inputs(target)  # raises NotFound if no forensic inputs

    risk_free = risk_free_rate()
    wacc = compute_wacc(risk_free, core["net_debt"], core["equity"])
    dcf = compute_dcf(core["fcf_base"], wacc["value"])

    notes: list[str] = [
        f"As-of fiscal year {core['as_of_year']}; inputs from stored SEC XBRL (no re-fetch).",
        "EBITDA = operating income + D&A (n/a when D&A is untagged).",
        (
            "Net debt uses a directly reported SEC debt concept (with its coverage disclosed "
            "below) or a complete tagged component set, less same-period cash; missing inputs "
            "remain n/a."
        ),
        f"WACC assumptions: ERP {EQUITY_RISK_PREMIUM:.0%}, beta {BETA}, tax {TAX_RATE:.0%}, "
        f"cost-of-debt spread {COST_OF_DEBT_SPREAD:.0%} over the risk-free rate.",
        "WACC capital weights use reported book equity because market capitalization is not stored; "
        "the resulting FCFF DCF is illustrative rather than an institutional valuation opinion.",
    ]
    if risk_free is None:
        notes.append("Risk-free rate unavailable (FRED DGS10 unreachable); WACC/DCF reported as n/a.")
    else:
        notes.append(f"Risk-free rate = latest FRED DGS10 = {risk_free:.2%}.")
    if core["ebitda"] is None:
        notes.append("EBITDA is n/a (D&A untagged); EBITDA-based reads degrade gracefully.")
    if core["net_debt"] is None:
        notes.append("Net debt is n/a because complete debt and same-period cash were not both available.")
    elif core["net_debt_basis"]:
        notes.append(f"Net debt basis: {core['net_debt_basis']}.")
    if core["fcf_base"] is None:
        notes.append(
            "FCFF base is n/a (CFO or capex untagged); DCF enterprise value is n/a."
        )
    elif core["fcf_base"] <= 0:
        notes.append(
            "FCFF base is non-positive; a growing Gordon-model enterprise value is not computed."
        )
    elif core.get("fcf_interest_omitted"):
        notes.append(
            "Interest expense is not separately tagged (e.g. netted into other income/expense); "
            "the after-tax interest add-back is omitted, so FCFF ≈ CFO − capex — a "
            "conservative approximation, disclosed rather than imputed."
        )

    return {
        "workspace_id": workspace_id,
        "target_name": target.name,
        "ebitda": core["ebitda"],
        "net_debt": core["net_debt"],
        "wacc": wacc,
        "dcf": dcf,
        "notes": notes,
        "generated_at": now_utc(),
    }


# --- LBO returns model ------------------------------------------------------

def _lbo_point(ebitda: float, entry_multiple: float, exit_multiple: float,
               leverage: float, hold_years: int, ebitda_cagr: float) -> dict:
    """Single-scenario LBO returns. Returns entry/exit EV & equity, MOIC, IRR (any may be None)."""
    entry_ev = entry_multiple * ebitda
    entry_debt = leverage * ebitda
    entry_equity = entry_ev - entry_debt

    exit_ebitda = ebitda * (1.0 + ebitda_cagr) ** hold_years
    exit_ev = exit_multiple * exit_ebitda

    # Simple debt paydown: cumulative FCF proxy = fcf_conversion * EBITDA in each held year.
    cumulative_fcf = sum(
        LBO_FCF_CONVERSION * ebitda * (1.0 + ebitda_cagr) ** y
        for y in range(1, hold_years + 1)
    )
    exit_debt = max(0.0, entry_debt - cumulative_fcf)
    exit_equity = exit_ev - exit_debt

    moic = None
    irr = None
    if entry_equity and entry_equity > 0:
        moic = exit_equity / entry_equity
        if moic > 0 and hold_years > 0:
            irr = moic ** (1.0 / hold_years) - 1.0
    return {
        "entry_ev": round(entry_ev, 2),
        "entry_equity": round(entry_equity, 2),
        "exit_ev": round(exit_ev, 2),
        "exit_equity": round(exit_equity, 2),
        "moic": round(moic, 4) if moic is not None else None,
        "irr": round(irr, 4) if irr is not None else None,
    }


def _sensitivity_axis(center: float) -> list[float]:
    """5 values spanning center +/- 2 (step 1), guarding against non-positive multiples."""
    step = (2.0 * SENSITIVITY_DELTA) / (SENSITIVITY_STEPS - 1)
    return [round(max(0.0, center - SENSITIVITY_DELTA + i * step), 2) for i in range(SENSITIVITY_STEPS)]


def compute_lbo(ebitda: float | None, inputs: dict) -> dict:
    """LBO result + entry x exit sensitivity grid. If EBITDA is None, returns nulls + a note."""
    entry_m = inputs["entry_multiple"]
    exit_m = inputs["exit_multiple"]
    leverage = inputs["leverage"]
    hold_years = int(inputs["hold_years"])
    cagr = inputs["ebitda_cagr"]

    assumptions = [
        f"Entry EV = {entry_m}x EBITDA; entry net debt = {leverage}x EBITDA (equity funds the rest).",
        f"EBITDA compounds at {cagr:.1%}/yr over a {hold_years}-year hold.",
        f"Exit EV = {exit_m}x exit-year EBITDA.",
        f"Debt paid down by a FCF proxy = {LBO_FCF_CONVERSION:.0%} of each year's EBITDA "
        f"(stand-in for FCF after interest, cash taxes, and capex).",
        "MOIC = exit equity / entry equity; IRR = MOIC^(1/hold_years) - 1.",
        "Sensitivity grid spans entry & exit multiples +/- 2 turns in 5 steps.",
    ]
    entry_axis = _sensitivity_axis(entry_m)
    exit_axis = _sensitivity_axis(exit_m)

    if ebitda is None or ebitda <= 0:
        note = (
            "EBITDA is n/a (D&A untagged) so LBO returns cannot be computed; "
            "provide a company with tagged D&A."
            if ebitda is None
            else "EBITDA is non-positive; LBO returns are undefined."
        )
        empty_grid = [[None for _ in exit_axis] for _ in entry_axis]
        return {
            "entry_ev": None,
            "entry_equity": None,
            "exit_ev": None,
            "exit_equity": None,
            "irr": None,
            "moic": None,
            "inputs": inputs,
            "sensitivity": {
                "entry_multiples": entry_axis,
                "exit_multiples": exit_axis,
                "irr_grid": empty_grid,
                "moic_grid": [[None for _ in exit_axis] for _ in entry_axis],
            },
            "assumptions": assumptions + [note],
            "generated_at": now_utc(),
        }

    base = _lbo_point(ebitda, entry_m, exit_m, leverage, hold_years, cagr)

    irr_grid: list[list[float | None]] = []
    moic_grid: list[list[float | None]] = []
    for em in entry_axis:
        irr_row: list[float | None] = []
        moic_row: list[float | None] = []
        for xm in exit_axis:
            if em <= 0:
                irr_row.append(None)
                moic_row.append(None)
                continue
            pt = _lbo_point(ebitda, em, xm, leverage, hold_years, cagr)
            irr_row.append(pt["irr"])
            moic_row.append(pt["moic"])
        irr_grid.append(irr_row)
        moic_grid.append(moic_row)

    return {
        "entry_ev": base["entry_ev"],
        "entry_equity": base["entry_equity"],
        "exit_ev": base["exit_ev"],
        "exit_equity": base["exit_equity"],
        "irr": base["irr"],
        "moic": base["moic"],
        "inputs": inputs,
        "sensitivity": {
            "entry_multiples": entry_axis,
            "exit_multiples": exit_axis,
            "irr_grid": irr_grid,
            "moic_grid": moic_grid,
        },
        "assumptions": assumptions,
        "generated_at": now_utc(),
    }


def run_lbo(session: Session, workspace_id: str, inputs: dict) -> dict:
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set for this workspace; ingest a company first.")
    core = _core_inputs(target)  # raises NotFound if no forensic inputs
    return compute_lbo(core["ebitda"], inputs)


# --- Optional red-flag hook (spliced by the integration agent) --------------

def risk_flags(session: Session, workspace_id: str) -> list[dict]:
    """Elevated-leverage red flag from the target's own net-debt/EBITDA, if both are available."""
    target = get_target(session, workspace_id)
    if target is None:
        return []
    try:
        core = _core_inputs(target)
    except NotFound:
        return []
    ebitda, net_debt = core["ebitda"], core["net_debt"]
    if ebitda is None or ebitda <= 0 or net_debt is None or net_debt <= 0:
        return []
    lev = net_debt / ebitda
    if lev < 4.0:
        return []
    severity, score = ("high", 7) if lev >= 6.0 else ("medium", 6)
    return [{
        "risk_category": "debt_liquidity",
        "risk_category_label": "Debt & liquidity",
        "title": "Elevated net leverage",
        "finding": (
            f"{target.name} carries net debt of ${net_debt/1e6:,.0f}M against EBITDA of "
            f"${ebitda/1e6:,.0f}M ({lev:.1f}x net-debt/EBITDA in FY{core['as_of_year']}), which "
            f"constrains incremental buyout leverage and raises refinancing sensitivity."
        ),
        "severity": severity,
        "severity_score": score,
        "likelihood": "high" if score >= 6 else "medium",
        "confidence": 0.82,
        "workstream_owner": "financial",
        "follow_up_question": (
            "What is the debt maturity and covenant package, and how much additional leverage can "
            "EBITDA support under a buyout structure?"
        ),
        "evidence": {
            "claim": f"FY{core['as_of_year']} net-debt/EBITDA is {lev:.1f}x.",
            "claim_type": "calculation",
            "evidence_text": (
                f"Net debt ${net_debt/1e6:,.0f}M / EBITDA ${ebitda/1e6:,.0f}M = {lev:.1f}x "
                f"(EBITDA = operating income + D&A; net debt = reported debt - cash; debt basis: "
                f"{core['net_debt_basis'] or 'complete tagged debt components'}, SEC XBRL)."
            ),
            "source_name": f"{target.name} FY{core['as_of_year']} 10-K (XBRL company facts)",
            "source_type": "xbrl",
            "source_url": None,
            "source_date": target.fiscal_year_end,
            "source_section": "XBRL company facts",
            "confidence": 0.82,
            "agent_name": "valuation_analyst",
        },
    }]
