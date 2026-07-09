# GovCon, macro & multi-year trends

Three keyless, real-data features sit on top of the SEC EDGAR core flow. They extend a first-pass read
from a single fiscal year of one filing to a **multi-year trajectory**, a **macro backdrop**, and — for
federal contractors — a **federal-contract diligence workstream**. All figures are real; nothing is
fabricated. This document covers each feature end to end: the endpoints, what they return, the
derivations, how they fold into the analysis pack, and the honest limitations.

| Feature | Source | Endpoint(s) | Backend module(s) |
|---|---|---|---|
| **Trends** — multi-year revenue + margin history, revenue CAGR | SEC XBRL (same company facts) | `GET /api/workspaces/{id}/trends` → `FinancialTrends` | `sec_financials.extract_trends`, `financial_benchmark_service.get_trends` |
| **Macro** — sector-relevant macro overlay | FRED (keyless `fredgraph.csv`) | `GET /api/workspaces/{id}/macro` → `MacroOverlay` | `fred_service` |
| **GovCon** — federal contract profile | USAspending.gov (keyless) | `GET` / `POST /api/workspaces/{id}/govcon` → `GovConProfile` | `usaspending_service`, `govcon_service` |

The contract shapes are in [`docs/CONTRACTS.md`](./CONTRACTS.md); TypeScript mirrors in
`apps/web/src/lib/types.ts` (`FinancialTrends`, `TrendPoint`, `MacroOverlay`, `MacroSeries`,
`GovConProfile`, `AgencyShare`, `GovConAward`, `Recompete`).

---

## 1. Multi-year trends (SEC XBRL)

### What it returns

`GET /api/workspaces/{id}/trends` → `FinancialTrends` (404 when no trend data is available):

```json
{ "workspace_id", "target_name", "years": ["2021", ... "2025"],
  "rows": [ { "year", "revenue"|null, "gross_margin"|null,
              "operating_margin"|null, "net_margin"|null, "rnd_pct"|null } ],
  "revenue_cagr"|null, "generated_at" }
```

### Derivation

The trend uses the **same** company-facts XBRL document already pulled at ingestion — no extra network
call. `sec_financials.extract_trends(facts, n=5)`:

1. For each relevant us-gaap concept family (revenue, gross profit, cost of revenue, operating income,
   net income, R&D), it collects **annual** duration facts and maps them by fiscal year
   (`_annual_by_year` keys on the period-end year `YYYY`, so the latest report per year wins).
2. It takes the **last `n` = 5** fiscal years present for revenue and, per year, computes revenue plus
   gross / operating / net margin and R&D % (gross profit falls back to `revenue − cost of revenue` when
   `GrossProfit` is not reported that year). Any metric missing for a year is `null`, never guessed.
3. **Revenue CAGR** over the span: `(revenue_last / revenue_first) ** (1 / years_between) − 1`, computed
   only when both endpoints are positive and the span is ≥ 1 year.

This is computed once at ingestion (`sec_ingestion_service` stores it in `target.financials["trends"]`).
`financial_benchmark_service.get_trends` reads it back and adds `workspace_id`, `target_name`, and
`generated_at`; the router serves it and returns **404** if the target has no financials or no trend rows.

### How it folds into the pack

- **Evidence.** When a CAGR is present, `analysis_service.run_full_analysis` writes a `calculation`
  Evidence row (`source_type "xbrl"`) — e.g. *"revenue CAGR was 12.3% over FY2021–FY2025"* — so the
  multi-year read is cited like any other number.
- **IC memo.** The memo's *Financial Profile (SEC XBRL)* table appends a **"Revenue CAGR (N-yr)"** row
  (with the CAGR evidence ref) when trend data exists, so the reader sees trajectory next to the latest-FY
  snapshot.
- **Frontend.** The trend rows are chartable with the existing Recharts pattern (see `BenchmarkChart.tsx`).

---

## 2. Macro overlay (FRED, keyless)

### What it returns

`GET /api/workspaces/{id}/macro` → `MacroOverlay` (404 if no target is set):

```json
{ "workspace_id", "target_name", "sector", "commentary",
  "series": [ { "series_id", "label", "unit", "note",
                "latest_value", "latest_date", "yoy_change"|null,
                "points": [ { "date", "value" } ] } ],
  "generated_at" }
```

### Derivation

`fred_service` pulls each series from the **keyless** graph CSV endpoint
`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series_id>` — no API key, one GET per series.

**Curated series** (`SERIES`), each with a label, unit, and a one-line diligence "note":

| Series ID | Label | Note (why it matters) |
|---|---|---|
| `FEDFUNDS` | Federal funds rate | Cost of capital / discount-rate pressure |
| `DGS10` | 10-year Treasury yield | Long-rate / valuation discount proxy |
| `CPIAUCSL` | CPI (all urban) | Inflation / input-cost pressure |
| `UNRATE` | Unemployment rate | Labor market / demand proxy |
| `INDPRO` | Industrial production | Industrial / manufacturing demand |
| `GDPC1` | Real GDP | Broad demand backdrop |

**Sector → series mapping** (`sectors_series`). Every target gets a **baseline** of `FEDFUNDS` +
`CPIAUCSL`; then the target's SEC `sicDescription` sector string is keyword-matched to add the most
relevant extras:

| Sector keywords | Added series |
|---|---|
| manufactur, industrial, machinery, semiconductor, hardware, electronic | `INDPRO` |
| bank, financ, insurance, real estate, reit | `DGS10`, `UNRATE` |
| retail, consumer, apparel, restaurant, food | `UNRATE`, `CPIAUCSL` |
| software, prepackaged, services-computer, internet | `DGS10` |

Per series, `_fetch_series` parses the CSV, drops missing points (`.` / empty), keeps the **last ~60
observations** (a compact ~5-year view), and computes a **YoY change** from the observation ~12 periods
back. `commentary` is a deterministic one-liner over the latest values (e.g. *"Federal funds rate 4.33%;
CPI +2.9% YoY"*). Any series that fails to fetch is silently skipped — the overlay degrades gracefully
rather than erroring.

### How it folds into the pack

Macro is **context, not a forecast**. It gives the diligence team a real, current backdrop to reason about
rate/inflation/demand sensitivity for the target's sector — it does not drive a valuation or a projection,
and it does not (today) generate its own risk findings. The `note` on each series frames the diligence
question the reader should ask (e.g. is this demand thesis coupled to the industrial cycle?).

---

## 3. GovCon federal-contract profile (USAspending, keyless)

The defense / GovCon extension. Given a recipient (company) name, it pulls **real federal contract award
history** from USAspending.gov and derives the concentration and recompete signals a GovCon investor cares
about most.

### Endpoints

- `POST /api/workspaces/{id}/govcon` `{recipient_name?}` → `GovConProfile`. Fetches from USAspending,
  upserts the profile, then **re-runs the full analysis** so GovCon findings/questions and the memo's
  GovCon section fold in. Upstream failure → **502**. If `recipient_name` is omitted, it uses the
  workspace target's name.
- `GET /api/workspaces/{id}/govcon` → `GovConProfile` (404 until first fetched).

### What it returns

```json
{ "id", "workspace_id", "recipient_name",
  "total_obligations", "award_count",
  "top_agency"|null, "top_agency_pct"|null,
  "agency_concentration": [ { "agency"|null, "amount", "pct"|null } ],
  "top_awards": [ { "award_id", "recipient", "agency", "sub_agency",
                    "amount"|null, "description", "pop_end"|null, "pop_start"|null } ],
  "recompete": { "count", "value",
                 "awards": [ { "award_id", "agency", "amount"|null, "pop_end"|null } ] },
  "created_at" }
```

### Derivation

`usaspending_service.award_profile(recipient_name)` hits the **keyless** `api.usaspending.gov/api/v2`, all
filtered to **definitive contract award types A–D** (excludes IDV / ceiling vehicles). `clean_recipient`
first trims corporate suffixes (`INC`, `CORP`, `HOLDINGS`, `LLC`, …) that hurt the fuzzy recipient search.

- **Total obligations + award count.** `spending_by_category/awarding_agency` (top 10 agencies) gives the
  per-agency obligated amounts, summed to **total obligations**; `spending_by_award_count` gives the
  contract **award count**.
- **Agency concentration** = each agency's share of total obligations; `top_agency` / `top_agency_pct` are
  the largest agency and its share. **This is the headline GovCon risk**: how dependent the business is on
  a single agency's budget, appropriations, and recompete cycle.
- **Top awards.** `spending_by_award` sorted by award amount descending, limit **25** (the 10 largest are
  stored for display). The wider 25-row scan is deliberate — see the recompete limitation below.
- **Recompete exposure** = of those scanned awards, the ones whose **period-of-performance current end
  date** falls within the next **~24 months** (`RECOMPETE_WINDOW_DAYS = 730`, from today). Returns the
  count, the summed value, and each award's id / agency / amount / `pop_end`. These are the awards the
  company must **defend as incumbent** at recompete; losing one directly reduces revenue.

`govcon_service.fetch` upserts a single `GovConProfile` row per workspace (`models/govcon.py`).

### How it folds into the analysis

On `POST /govcon`, after the profile is stored, `analysis_service.run_full_analysis` re-runs and pulls the
profile via `govcon_service.get_optional`. `risk_analyst.govcon_flags(profile)` then emits real
`govcon_risk` risk findings (workstream owner `govcon`), each cited to a **`source_type: "usaspending"`**
Evidence row:

| Signal (from USAspending) | Finding | Severity |
|---|---|---|
| Top agency ≥ 50% of obligations | *"Federal revenue concentrated in {agency}"* | high (7) if ≥ 65%, else medium (6) |
| ≥ 1 major award up for recompete within 24 months | *"Major awards up for recompete within 24 months"* | high (7) if recompete value > 20% of total, else medium (5) |

The IC memo also gains a **"Federal Contract Profile (GovCon)"** section (only when
`total_obligations > 0`): total obligations + action count, the top-agency concentration line, and the
recompete-exposure line. Because generation is idempotent, re-fetching GovCon rebuilds the whole pack in
sync.

### Real example — Leidos (LDOS)

Leidos is a large defense/IT services prime. A GovCon fetch returns roughly **~$128B in federal contract
obligations**, with the **Department of Defense at ~52%** of obligations — clearing the 50% concentration
threshold and producing a medium-to-high `govcon_risk` finding about single-agency (DoD budget /
appropriations) dependence, with the recompete scan surfacing the largest awards whose PoP ends inside the
24-month window. This is exactly the profile that makes a target suitable for **defense / GovCon
diligence**, and it is all real, cited USAspending data.

---

## Honest limitations

These features widen the read but do **not** remove the project's honesty constraints — they add their
own, stated plainly:

- **Recompete depends on PoP-end availability.** Recompete is derived from the *period-of-performance
  current end date*, which large parent/ceiling vehicles often leave **null**. For a company whose very
  largest awards omit `pop_end`, the recompete count can read **0** even though real recompete exposure
  exists. The 25-row scan (vs. the 10 stored for display) is a partial mitigation — it catches
  smaller awards that *do* carry an end date — but a 0 here means *"not visible in this field,"* not
  *"no recompete risk."*
- **Recipient-name matching is fuzzy.** USAspending is searched by cleaned recipient name, not a hard
  identifier, so figures reflect what maps to that name (subsidiaries and name variants can shift totals).
  Treat obligations and concentration as directional, to confirm against the entity's own reporting.
- **Contract awards only (types A–D).** IDV ceilings and grants/other assistance are intentionally
  excluded, so "total obligations" is federal **contract** obligations, not all federal dollars.
- **XBRL fiscal-year gaps.** Multi-year trends and CAGR depend on annual XBRL periods being present and
  cleanly tagged; a company that restated, changed fiscal years, or tagged a concept inconsistently can
  yield a gap (`null`) or a shorter-than-5-year span. Missing years are omitted, never interpolated.
- **Market multiples are still omitted.** None of these features add valuation — market cap, enterprise
  value, and EV/Revenue remain `null` (no free source), consistent with the rest of the pack.
- **Macro is context, not a forecast.** The FRED overlay is a real, current backdrop for reasoning about
  sensitivity — it is not a projection, does not drive a number, and does not by itself generate findings.

Every figure in all three features is real and (for trends and GovCon) cited to an Evidence row, so a
reviewer can check it — an XBRL concept for trends, a `usaspending` award fact for GovCon. See
[`docs/evidence-model.md`](./evidence-model.md).

---

## Related docs

[`data-sources.md`](./data-sources.md) ·
[`diligence-methodology.md`](./diligence-methodology.md) ·
[`architecture.md`](./architecture.md) ·
[`evidence-model.md`](./evidence-model.md) ·
[`CONTRACTS.md`](./CONTRACTS.md)
