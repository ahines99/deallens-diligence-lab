"""Derive a real financial summary from SEC XBRL company facts.

Uses standard us-gaap concepts with fallbacks. Every derived figure keeps a reference to the
underlying XBRL point (concept, period end, accession, form) so it can be cited as evidence.
Margins/growth are deterministic calculations over the reported values.
"""
from __future__ import annotations

from dataclasses import dataclass

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
DEBT_CONCEPTS = ["LongTermDebtNoncurrent", "LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"]
ASSETS_CONCEPTS = ["Assets"]


@dataclass
class Point:
    value: float
    end: str
    accession: str
    form: str
    concept: str


def _point(pt: dict, concept: str) -> Point:
    return Point(
        value=float(pt["val"]),
        end=pt.get("end", ""),
        accession=pt.get("accn", ""),
        form=pt.get("form", ""),
        concept=concept,
    )


def _latest(facts: dict, concepts: list[str], instant: bool = False) -> Point | None:
    concept, pts = edgar_client.pick_concept(facts, concepts, instant=instant)
    if not concept or not pts:
        return None
    return _point(pts[-1], concept)


def _latest_two(facts: dict, concepts: list[str]) -> tuple[Point | None, Point | None]:
    concept, pts = edgar_client.pick_concept(facts, concepts, instant=False)
    if not concept or not pts:
        return None, None
    # De-duplicate by fiscal-year end, keep chronological.
    by_end: dict[str, dict] = {}
    for p in pts:
        by_end[p["end"][:4]] = p
    ordered = [by_end[k] for k in sorted(by_end)]
    latest = _point(ordered[-1], concept) if ordered else None
    prior = _point(ordered[-2], concept) if len(ordered) >= 2 else None
    return latest, prior


def _ratio(num: Point | None, den: Point | None) -> float | None:
    if not num or not den or not den.value:
        return None
    return round(num.value / den.value, 4)


def _annual_by_year(
    facts: dict, concepts: list[str], instant: bool = False, unit: str = "USD"
) -> dict[str, float]:
    """Map fiscal-year (YYYY) -> value for the first concept with annual data."""
    _, pts = edgar_client.pick_concept(facts, concepts, instant=instant, unit=unit)
    return {p["end"][:4]: float(p["val"]) for p in pts}


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
    "short_debt": ["ShortTermBorrowings", "DebtCurrent"],
    "ltd": ["LongTermDebtNoncurrent", "LongTermDebt"],
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
    for key, concepts in _BAL.items():
        maps[key] = _annual_by_year(facts, concepts, instant=True)
    for key, concepts in _FLOW.items():
        maps[key] = _annual_by_year(facts, concepts, instant=False)
    maps["shares_out"] = _annual_by_year(facts, _SHARES_INSTANT, instant=True, unit="shares")
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

    gross_profit = _latest(facts, GROSS_PROFIT_CONCEPTS)
    if gross_profit is None and revenue is not None:
        cost = _latest(facts, COST_OF_REVENUE_CONCEPTS)
        if cost is not None and cost.end == revenue.end:
            gross_profit = Point(
                value=revenue.value - cost.value,
                end=revenue.end,
                accession=cost.accession,
                form=cost.form,
                concept=f"{revenue.concept} - {cost.concept}",
            )

    operating_income = _latest(facts, OPERATING_INCOME_CONCEPTS)
    net_income = _latest(facts, NET_INCOME_CONCEPTS)
    rnd = _latest(facts, RND_CONCEPTS)
    cash = _latest(facts, CASH_CONCEPTS, instant=True)
    debt = _latest(facts, DEBT_CONCEPTS, instant=True)
    assets = _latest(facts, ASSETS_CONCEPTS, instant=True)

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
