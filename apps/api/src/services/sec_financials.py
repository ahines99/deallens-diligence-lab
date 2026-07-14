"""Derive a real financial summary from SEC XBRL company facts.

Uses standard us-gaap concepts with fallbacks. Every derived figure keeps a reference to the
underlying XBRL point (concept, period end, accession, form) so it can be cited as evidence.
Margins/growth are deterministic calculations over the reported values.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from src.services import edgar_client

REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]
GROSS_PROFIT_CONCEPTS = ["GrossProfit"]
COST_OF_REVENUE_CONCEPTS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
NET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
RND_CONCEPTS = ["ResearchAndDevelopmentExpense"]
CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
]
DEBT_CONCEPTS = [
    "DebtLongtermAndShorttermCombinedAmount",
    "LongTermDebt",
    "LongTermDebtNoncurrent",
]
ASSETS_CONCEPTS = ["Assets"]


@dataclass
class Point:
    value: float
    end: str
    accession: str
    form: str
    concept: str
    frame: str
    fy: str


_FRAME_YEAR = re.compile(r"^CY(\d{4})")


def _period_year(point: dict) -> str:
    """Return the fiscal label carried by an annual duration frame when available.

    This matters for 52/53-week issuers whose FY2024 can end in early January 2025. Instant facts
    are aligned separately to the duration fact's exact period end; their calendar-quarter frame is
    not assumed to represent the issuer's fiscal year.
    """
    fiscal_year = str(point.get("fy") or "").strip()
    if re.fullmatch(r"\d{4}", fiscal_year):
        return fiscal_year
    match = _FRAME_YEAR.match(point.get("frame", ""))
    if match:
        return match.group(1)
    return point.get("end", "")[:4]


def _point(pt: dict, concept: str) -> Point:
    return Point(
        value=float(pt["val"]),
        end=pt.get("end", ""),
        accession=pt.get("accn", ""),
        form=pt.get("form", ""),
        concept=concept,
        frame=pt.get("frame", ""),
        fy=str(pt.get("fy") or ""),
    )


def _latest(facts: dict, concepts: list[str], instant: bool = False) -> Point | None:
    concept, pts = edgar_client.pick_concept(facts, concepts, instant=instant)
    if not concept or not pts:
        return None
    return _point(pts[-1], concept)


def _for_period(
    facts: dict,
    concepts: list[str],
    period_year: str,
    instant: bool = False,
    period_end: str | None = None,
) -> Point | None:
    """Return a fact only when it belongs to the requested annual reporting period."""
    concept, points = edgar_client.pick_concept(facts, concepts, instant=instant)
    if not concept:
        return None
    if instant and period_end:
        exact = [point for point in points if point.get("end") == period_end]
        return _point(exact[-1], concept) if exact else None
    matches = [point for point in points if _period_year(point) == period_year]
    return _point(matches[-1], concept) if matches else None


def _latest_two(facts: dict, concepts: list[str]) -> tuple[Point | None, Point | None]:
    concept, pts = edgar_client.pick_concept(facts, concepts, instant=False)
    if not concept or not pts:
        return None, None
    # De-duplicate by the SEC annual frame, not the raw calendar end year.
    by_end: dict[str, dict] = {}
    for p in pts:
        by_end[_period_year(p)] = p
    ordered = [by_end[k] for k in sorted(by_end)]
    latest = _point(ordered[-1], concept) if ordered else None
    prior = _point(ordered[-2], concept) if len(ordered) >= 2 else None
    return latest, prior


def _ratio(num: Point | None, den: Point | None) -> float | None:
    if not num or not den or not den.value:
        return None
    if num.fy and den.fy and num.fy != den.fy:
        return None
    if num.frame and den.frame and _FRAME_YEAR.match(num.frame) and _FRAME_YEAR.match(den.frame):
        if _FRAME_YEAR.match(num.frame).group(1) != _FRAME_YEAR.match(den.frame).group(1):
            return None
    return round(num.value / den.value, 4)


def _annual_by_year(
    facts: dict,
    concepts: list[str],
    instant: bool = False,
    unit: str = "USD",
    duration_periods: dict[str, str] | None = None,
) -> dict[str, float]:
    """Map fiscal-year (YYYY) -> value for the first concept with annual data."""
    _, pts = edgar_client.pick_concept(facts, concepts, instant=instant, unit=unit)
    if instant and duration_periods is not None:
        return {
            duration_periods[p["end"]]: float(p["val"])
            for p in pts
            if p.get("end") in duration_periods
        }
    return {_period_year(p): float(p["val"]) for p in pts}


def _duration_periods(facts: dict) -> dict[str, str]:
    """Map exact annual duration end dates to the fiscal labels used downstream."""

    _, points = edgar_client.pick_concept(facts, REVENUE_CONCEPTS, instant=False)
    return {
        point["end"]: _period_year(point)
        for point in points
        if point.get("end")
    }


# --- Extended concepts for Quality-of-Earnings / forensics / valuation ------
# Balance-sheet (instant) concepts.
_BAL = {
    "assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet"],
    "payables": ["AccountsPayableCurrent", "AccountsPayableTradeCurrent"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
    "ltd_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    # Keep these components mutually exclusive. ``DebtCurrent`` can include current maturities,
    # while ``LongTermDebt`` can include both current and noncurrent portions; using either as a
    # fallback here would silently double-count the separately extracted components.
    "short_debt": ["ShortTermBorrowings"],
    "ltd": ["LongTermDebtNoncurrent"],
    "cash": CASH_CONCEPTS,
}
# Income-statement / cash-flow (duration) concepts.
_FLOW = {
    "revenue": REVENUE_CONCEPTS,
    "cogs": COST_OF_REVENUE_CONCEPTS,
    "gross_profit": GROSS_PROFIT_CONCEPTS,
    "operating_income": OPERATING_INCOME_CONCEPTS,
    "net_income": NET_INCOME_CONCEPTS,
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    # D&A is sparsely tagged — several fallbacks; may be absent (degrade gracefully).
    "da": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "Depreciation",
        "DepreciationAndAmortization",
    ],
    "tax": ["IncomeTaxExpenseBenefit"],
    "interest": ["InterestExpense", "InterestExpenseDebt"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"],
    "sbc": ["ShareBasedCompensation"],
    "rnd": RND_CONCEPTS,
}
_SHARES_INSTANT = ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"]
_SHARES_DILUTED = ["WeightedAverageNumberOfDilutedSharesOutstanding"]


def extract_forensic_inputs(facts: dict, n: int = 6) -> dict:
    """Per-fiscal-year maps of the concepts needed for QoE, forensic scores, and valuation.

    Returns {"years": [YYYY...], "by_year": {YYYY: {field: value|None}}}. Fields may be None when a
    concept isn't tagged for that year (notably D&A) — downstream math must degrade gracefully.
    """
    maps: dict[str, dict[str, float]] = {}
    duration_periods = _duration_periods(facts)
    for key, concepts in _BAL.items():
        maps[key] = _annual_by_year(
            facts,
            concepts,
            instant=True,
            duration_periods=duration_periods,
        )
    for key, concepts in _FLOW.items():
        maps[key] = _annual_by_year(facts, concepts, instant=False)
    maps["shares_out"] = _annual_by_year(
        facts,
        _SHARES_INSTANT,
        instant=True,
        unit="shares",
        duration_periods=duration_periods,
    )
    maps["shares_diluted"] = _annual_by_year(facts, _SHARES_DILUTED, instant=False, unit="shares")

    year_set: set[str] = set()
    for m in maps.values():
        year_set |= set(m.keys())
    years = sorted(year_set)[-n:]
    by_year = {y: {field: maps[field].get(y) for field in maps} for y in years}
    return {"years": years, "by_year": by_year}


def extract_trends(facts: dict, n: int = 5) -> dict:
    """Multi-year revenue + margin trend (last `n` fiscal years) from XBRL company facts."""
    rev = _annual_by_year(facts, REVENUE_CONCEPTS)
    gp = _annual_by_year(facts, GROSS_PROFIT_CONCEPTS)
    cost = _annual_by_year(facts, COST_OF_REVENUE_CONCEPTS)
    oi = _annual_by_year(facts, OPERATING_INCOME_CONCEPTS)
    ni = _annual_by_year(facts, NET_INCOME_CONCEPTS)
    rnd = _annual_by_year(facts, RND_CONCEPTS)
    years = sorted(rev.keys())[-n:]

    def margin(numerator: dict, y: str) -> float | None:
        r = rev.get(y)
        v = numerator.get(y)
        return round(v / r, 4) if (v is not None and r) else None

    rows = []
    for y in years:
        r = rev.get(y)
        g = gp.get(y)
        if g is None and r is not None and y in cost:
            g = r - cost[y]
        rows.append(
            {
                "year": y,
                "revenue": r,
                "gross_margin": (round(g / r, 4) if (g is not None and r) else None),
                "operating_margin": margin(oi, y),
                "net_margin": margin(ni, y),
                "rnd_pct": margin(rnd, y),
            }
        )

    revenue_cagr = None
    if len(years) >= 2 and rev.get(years[0]) and rev.get(years[-1]) and rev[years[0]] > 0:
        span = int(years[-1]) - int(years[0])
        if span > 0:
            revenue_cagr = round((rev[years[-1]] / rev[years[0]]) ** (1 / span) - 1, 4)

    return {"years": years, "rows": rows, "revenue_cagr": revenue_cagr}


def extract_financials(facts: dict) -> dict:
    """Return a dict of real financials with source points, or Nones where unavailable."""
    revenue, revenue_prior = _latest_two(facts, REVENUE_CONCEPTS)
    period_year = (
        _period_year({"fy": revenue.fy, "frame": revenue.frame, "end": revenue.end})
        if revenue
        else ""
    )

    gross_profit = _for_period(facts, GROSS_PROFIT_CONCEPTS, period_year)
    if gross_profit is None and revenue is not None:
        cost = _for_period(facts, COST_OF_REVENUE_CONCEPTS, period_year)
        if cost is not None and cost.end == revenue.end:
            gross_profit = Point(
                value=revenue.value - cost.value,
                end=revenue.end,
                accession=cost.accession,
                form=cost.form,
                concept=f"{revenue.concept} - {cost.concept}",
                frame=revenue.frame,
                fy=revenue.fy,
            )

    operating_income = _for_period(facts, OPERATING_INCOME_CONCEPTS, period_year)
    net_income = _for_period(facts, NET_INCOME_CONCEPTS, period_year)
    rnd = _for_period(facts, RND_CONCEPTS, period_year)
    period_end = revenue.end if revenue else None
    cash = _for_period(
        facts, CASH_CONCEPTS, period_year, instant=True, period_end=period_end
    )
    debt = _for_period(
        facts, DEBT_CONCEPTS, period_year, instant=True, period_end=period_end
    )
    assets = _for_period(
        facts, ASSETS_CONCEPTS, period_year, instant=True, period_end=period_end
    )

    revenue_growth = None
    if revenue and revenue_prior and revenue_prior.value:
        revenue_growth = round((revenue.value - revenue_prior.value) / revenue_prior.value, 4)

    gross_margin = _ratio(gross_profit, revenue)
    operating_margin = _ratio(operating_income, revenue)
    net_margin = _ratio(net_income, revenue)
    rnd_pct = _ratio(rnd, revenue)

    def dump(p: Point | None) -> dict | None:
        if p is None:
            return None
        return {
            "value": p.value,
            "end": p.end,
            "accession": p.accession,
            "form": p.form,
            "concept": p.concept,
            "frame": p.frame,
            "fy": p.fy,
        }

    return {
        "fiscal_year_end": revenue.end if revenue else None,
        "revenue": revenue.value if revenue else None,
        "revenue_prior": revenue_prior.value if revenue_prior else None,
        "revenue_growth": revenue_growth,
        "gross_profit": gross_profit.value if gross_profit else None,
        "gross_margin": gross_margin,
        "operating_income": operating_income.value if operating_income else None,
        "operating_margin": operating_margin,
        "net_income": net_income.value if net_income else None,
        "net_margin": net_margin,
        "rnd": rnd.value if rnd else None,
        "rnd_pct": rnd_pct,
        "cash": cash.value if cash else None,
        "total_debt": debt.value if debt else None,
        "assets": assets.value if assets else None,
        "rule_of_40": (
            round(revenue_growth + operating_margin, 4)
            if revenue_growth is not None and operating_margin is not None
            else None
        ),
        "sources": {
            "revenue": dump(revenue),
            "gross_profit": dump(gross_profit),
            "operating_income": dump(operating_income),
            "net_income": dump(net_income),
            "rnd": dump(rnd),
            "cash": dump(cash),
            "total_debt": dump(debt),
        },
    }
