"""Derive a real financial summary from SEC XBRL company facts.

Uses standard us-gaap concepts with fallbacks. Every derived figure keeps a reference to the
underlying XBRL point (concept, period end, accession, form) so it can be cited as evidence.
Margins/growth are deterministic calculations over the reported values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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


# --- 10-Q quarterly extraction + trailing-twelve-month derivation (G11) -----
# Flow metrics eligible for quarterly extraction and TTM summation.
QUARTERLY_METRICS = {
    "revenue": REVENUE_CONCEPTS,
    "gross_profit": GROSS_PROFIT_CONCEPTS,
    "operating_income": OPERATING_INCOME_CONCEPTS,
    "net_income": NET_INCOME_CONCEPTS,
}

# The next quarter must start within this many days of the prior quarter's end (normally 1 day;
# a small tolerance absorbs 52/53-week calendar drift). Anything larger is a gap — never blended.
_CONTIGUITY_TOLERANCE_DAYS = 4


def _iso_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def _contiguous(prev_end: str | None, next_start: str | None) -> bool:
    prev = _iso_date(prev_end)
    nxt = _iso_date(next_start)
    if prev is None or nxt is None:
        return False
    return 0 <= (nxt - prev).days <= _CONTIGUITY_TOLERANCE_DAYS


def _derive_q4(annual_pts: list[dict], discrete: list[dict]) -> list[dict]:
    """Derive Q4 = FY − (Q1+Q2+Q3) when the FY period exactly spans three contiguous discrete
    quarters plus one missing quarter-length tail. XBRL reality: most issuers never tag Q4 as a
    discrete duration. Derived points are labeled ``derivation: "fy_minus_q123"``; when the FY
    period cannot be reconciled to the discrete quarters, nothing is derived (never impute)."""
    derived: list[dict] = []
    discrete_ends = {p.get("end") for p in discrete}
    for fy_pt in annual_pts:
        fy_start, fy_end = fy_pt.get("start") or "", fy_pt.get("end") or ""
        if not fy_start or not fy_end or fy_end in discrete_ends:
            continue  # no FY span, or Q4 is already tagged discretely
        inside = sorted(
            (
                p
                for p in discrete
                if (p.get("start") or "") >= fy_start and (p.get("end") or "") <= fy_end
            ),
            key=lambda p: p.get("end", ""),
        )
        if len(inside) != 3:
            continue
        if not _contiguous(fy_start, inside[0].get("start")):
            continue  # Q1 does not begin the FY period — the FY span is not Q1..Q3 + a tail
        if not all(
            _contiguous(inside[i].get("end"), inside[i + 1].get("start")) for i in range(2)
        ):
            continue
        q3_end = _iso_date(inside[2].get("end"))
        fye = _iso_date(fy_end)
        if q3_end is None or fye is None:
            continue
        tail_days = (fye - q3_end).days
        if not (edgar_client.QUARTER_MIN_DAYS <= tail_days <= edgar_client.QUARTER_MAX_DAYS):
            continue  # fiscal-year-end change or a hidden gap — do not derive
        derived.append(
            {
                "start": (q3_end + timedelta(days=1)).isoformat(),
                "end": fy_end,
                "val": float(fy_pt["val"]) - sum(float(p["val"]) for p in inside),
                "fy": fy_pt.get("fy"),
                "fp": "Q4",
                "form": fy_pt.get("form", ""),
                "accn": fy_pt.get("accn", ""),
                "derivation": "fy_minus_q123",
            }
        )
    return derived


def _ttm(points: list[dict]) -> tuple[float | None, dict]:
    """Sum the last four quarters ONLY when they are contiguous; otherwise (None, reason).

    ``points`` are quarterly dicts (discrete + derived) sorted by end date. A partial or
    gap-spanning sum is never returned.
    """

    def periods(pts: list[dict]) -> list[dict]:
        return [
            {"start": p.get("start"), "end": p.get("end"), "derivation": p.get("derivation")}
            for p in pts
        ]

    if len(points) < 4:
        return None, {
            "periods": periods(points),
            "reason": (
                f"only {len(points)} quarterly period(s) available; "
                "four contiguous quarters are required for a TTM sum"
            ),
        }
    last4 = points[-4:]
    for prev, nxt in zip(last4, last4[1:]):
        if not _contiguous(prev.get("end"), nxt.get("start")):
            return None, {
                "periods": periods(last4),
                "reason": (
                    f"quarters are not contiguous: gap between period ending {prev.get('end')} "
                    f"and period starting {nxt.get('start')}; TTM is not computed across gaps"
                ),
            }
    return sum(float(p["val"]) for p in last4), {"periods": periods(last4), "reason": None}


def extract_quarterly(facts: dict, n: int = 8) -> dict:
    """Last ``n`` quarters of flow metrics plus per-metric TTM from XBRL company facts.

    Returns {"quarters": [...], "ttm": {metric: value|None}, "ttm_basis": {metric: {...}}}.
    Q4 may be derived as FY − (Q1+Q2+Q3) (labeled in ``derived``/``derivation``); a metric's TTM
    is None with an explicit reason whenever four contiguous quarters cannot be established.
    """
    per_metric: dict[str, list[dict]] = {}
    for key, concepts in QUARTERLY_METRICS.items():
        concept, discrete = None, []
        for candidate in concepts:
            pts = edgar_client.quarterly_points(facts, candidate)
            if pts:
                concept, discrete = candidate, pts
                break
        if concept is None:
            per_metric[key] = []
            continue
        # Derive Q4 strictly from the same concept's annual series — concepts are never mixed.
        annual = edgar_client.annual_points(facts, concept)
        merged = discrete + _derive_q4(annual, discrete)
        merged.sort(key=lambda p: (p.get("end", ""), p.get("start", "")))
        per_metric[key] = merged

    ttm: dict[str, float | None] = {}
    ttm_basis: dict[str, dict] = {}
    for key, pts in per_metric.items():
        value, basis = _ttm(pts)
        ttm[key] = value
        ttm_basis[key] = basis

    rows: dict[str, dict] = {}
    for key, pts in per_metric.items():
        for p in pts:
            row = rows.setdefault(
                p.get("end", ""),
                {
                    "start": p.get("start"),
                    "end": p.get("end"),
                    "fy": str(p.get("fy") or "") or None,
                    "fp": p.get("fp") or None,
                    "form": p.get("form") or None,
                    "revenue": None,
                    "gross_profit": None,
                    "operating_income": None,
                    "net_income": None,
                    "derived": {},
                },
            )
            row[key] = float(p["val"])
            if p.get("derivation"):
                row["derived"][key] = p["derivation"]
    quarters = [rows[end] for end in sorted(rows)][-n:]
    return {"quarters": quarters, "ttm": ttm, "ttm_basis": ttm_basis}


# --- G12: XBRL segment-level revenue (dimensional facts) --------------------
# XBRL reality: the standard SEC Company Facts endpoint publishes only UNDIMENSIONED points —
# i.e. the consolidated total for each concept. True per-segment revenue lives on dimensional
# contexts (a member on a reporting axis such as us-gaap:StatementBusinessSegmentsAxis) and is NOT
# emitted by companyfacts; it exists only in the raw filing instance / frames. So for real filers
# this extractor almost always reports "consolidated only" — it NEVER imputes a segment split.
# When a facts payload does carry dimensional qualifiers (synthetic fixtures, or the rare source
# that preserves them under a per-point ``segments`` axis/member list), the members are read back
# verbatim into per-segment period series.
#
# Reporting axes we recognise, in preference order. Only ONE axis is used per company (the first
# present) so members from different breakdowns (business vs geography vs product) are never mixed
# into a single, double-counted list.
SEGMENT_AXIS_PRIORITY = [
    "us-gaap:StatementBusinessSegmentsAxis",
    "srt:ProductOrServiceAxis",
    "us-gaap:ProductOrServiceAxis",
    "us-gaap:StatementGeographicalAxis",
    "srt:StatementGeographicalAxis",
]
# A duration this long or longer is an annual segment figure (excludes ~91d quarters, ~182d halves).
_ANNUAL_MIN_DAYS = 340
# Segment members should sum to the consolidated total; a larger relative gap means an untagged
# Other/Corporate/eliminations member, i.e. only partial segment detail.
_SEGMENT_RECONCILE_TOLERANCE = 0.005

_CONSOLIDATED_ONLY_NOTE = (
    "segment detail not available in companyfacts (consolidated only)"
)


def _point_dimensions(point: dict) -> list[tuple[str, str]]:
    """Return the (axis, member) qualifiers carried by a companyfacts point.

    Standard SEC companyfacts points are undimensioned and return ``[]`` here. A dimensional point
    is expected to carry a ``segments`` list of ``{"dim"/"axis", "member"/"value"}`` entries.
    """
    dims = point.get("segments")
    if not isinstance(dims, list):
        return []
    out: list[tuple[str, str]] = []
    for entry in dims:
        if not isinstance(entry, dict):
            continue
        axis = entry.get("dim") or entry.get("axis") or ""
        member = entry.get("member") or entry.get("value") or ""
        if axis and member:
            out.append((axis, member))
    return out


def _clean_member(member: str) -> str:
    """Human label for an XBRL member QName (e.g. ``xyz:CloudServicesMember`` -> ``Cloud Services``)."""
    local = member.split(":")[-1]
    if local.endswith("Member"):
        local = local[: -len("Member")]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", local)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return spaced.strip() or local


def extract_segments(facts: dict, n: int = 6) -> dict:
    """Per-segment revenue time series from dimensional XBRL facts, or an honest "unavailable".

    Returns ``{"status", "axis", "segments", "note"}`` where ``segments`` is a list of
    ``{segment_name, member, source_concept, periods:[{period_end, revenue}]}`` ordered oldest
    period first. ``status`` is:

    * ``available`` — dimensional members found and (where a consolidated total exists) they
      reconcile to it;
    * ``partial``   — dimensional members found but they do not fully reconcile to the consolidated
      total for at least one period (an untagged Other/eliminations member is implied);
    * ``unavailable`` — no dimensional segment members are present (the companyfacts norm); the
      consolidated-only figures are all that exist. Segment splits are NEVER fabricated.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in REVENUE_CONCEPTS:
        series = us_gaap.get(concept, {}).get("units", {}).get("USD", [])
        # axis -> member -> period_end -> latest-filed point; plus consolidated (undimensioned).
        buckets: dict[str, dict[str, dict[str, dict]]] = {}
        consolidated: dict[str, dict] = {}
        for point in series:
            start, end = point.get("start"), point.get("end")
            if not start or not end:
                continue
            days = edgar_client._duration_days(start, end)
            if days is None or days < _ANNUAL_MIN_DAYS:
                continue
            dims = [d for d in _point_dimensions(point) if d[0] in SEGMENT_AXIS_PRIORITY]
            if not dims:
                if not _point_dimensions(point):  # truly undimensioned = consolidated total
                    _keep_latest(consolidated, end, point)
                continue
            if len(dims) != 1:
                continue  # a cross-tabulated cell (e.g. segment x geography) — never partial-count
            axis, member = dims[0]
            _keep_latest(buckets.setdefault(axis, {}).setdefault(member, {}), end, point)

        axis = next((a for a in SEGMENT_AXIS_PRIORITY if buckets.get(a)), None)
        if axis is None:
            continue

        segments = []
        for member, by_end in sorted(buckets[axis].items()):
            periods = [
                {"period_end": e, "revenue": float(by_end[e]["val"])}
                for e in sorted(by_end)
            ][-n:]
            segments.append(
                {
                    "segment_name": _clean_member(member),
                    "member": member,
                    "source_concept": concept,
                    "periods": periods,
                }
            )

        status, note = _reconcile_segments(buckets[axis], consolidated)
        return {"status": status, "axis": axis, "segments": segments, "note": note}

    return {
        "status": "unavailable",
        "axis": None,
        "segments": [],
        "note": _CONSOLIDATED_ONLY_NOTE,
    }


def _keep_latest(slot: dict[str, dict], end: str, point: dict) -> None:
    """Retain the most recently filed point per period end (amendment/restatement precedence)."""
    existing = slot.get(end)
    ordering = (point.get("filed", ""), point.get("accn", ""))
    if existing is None or ordering >= (existing.get("filed", ""), existing.get("accn", "")):
        slot[end] = point


def _reconcile_segments(
    by_member: dict[str, dict[str, dict]], consolidated: dict[str, dict]
) -> tuple[str, str | None]:
    """Reconcile the tagged segment members against the consolidated total, period by period."""
    ends = {e for member in by_member.values() for e in member}
    mismatches: list[str] = []
    for end in sorted(ends):
        total_point = consolidated.get(end)
        if total_point is None:
            continue
        total = float(total_point["val"])
        seg_sum = sum(
            float(member[end]["val"]) for member in by_member.values() if end in member
        )
        if abs(seg_sum - total) > max(_SEGMENT_RECONCILE_TOLERANCE * abs(total), 1.0):
            mismatches.append(
                f"segments sum to {seg_sum:,.0f} vs consolidated {total:,.0f} "
                f"for period ending {end}"
            )
    if mismatches:
        return "partial", (
            "segment members do not fully reconcile to consolidated revenue "
            "(an untagged Other/Corporate/eliminations member is implied): "
            + "; ".join(mismatches)
        )
    return "available", None


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
