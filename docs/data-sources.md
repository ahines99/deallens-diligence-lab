# Public data sources

DealLens Diligence Lab is a **public-data** copilot. Nothing in the pack is proprietary or paywalled —
the primary flow draws only on **free SEC EDGAR data** for real public companies, and three more **keyless
public sources are now live** (FRED macro, USAspending federal awards, and multi-year SEC XBRL trends).
This document lists each source, what it provides, how DealLens uses it, the operating notes, and whether
it is primary, a live extension, or a remaining wired extension.

> **Primary source.** The core flow is built on **SEC EDGAR** (`company_tickers.json`, submissions,
> companyfacts XBRL, and Archives filing documents). Creating a workspace with a ticker pulls this data
> live — there is no synthetic target. The same company-facts XBRL now also powers a **multi-year revenue
> and margin trend** (`sec_financials.extract_trends`) with a computed revenue CAGR.
>
> **Now-live extensions (no key).** **FRED** (macro overlay via the keyless fredgraph CSV),
> **USAspending.gov** (federal contract awards → agency concentration + recompete exposure), and
> **GDELT** (keyless news signals surfaced on the workspace `GET /news` tab) are implemented end to end
> and surface real data — see [`docs/govcon-and-macro.md`](./govcon-and-macro.md). GDELT results are
> labeled **unverified media** (real articles, but not evidence-grade) and are kept out of the evidence
> table. **OpenFIGI** and **SAM.gov** remain wired extension points: the interfaces are documented but
> not required for the core flow.

> **What is intentionally omitted.** Market **valuation multiples** (market cap, enterprise value,
> EV/Revenue) are **not populated** — there is no free, reliable source for them, and the project's rule
> is to omit rather than fabricate. Benchmarking is done on SEC-reported **fundamentals** only.

---

## Source table

| Source | What it provides | How DealLens uses it | Operating notes | Status |
|---|---|---|---|---|
| **SEC EDGAR APIs** | Ticker→CIK map (`company_tickers.json`), submissions history (10-K / 10-Q / 8-K metadata), **company facts** (standardized XBRL financials), and the primary filing documents (`Archives`) | Resolve a ticker to a CIK; ingest recent filings; derive real target & peer financials from company facts; **multi-year revenue/margin trends + revenue CAGR** (`extract_trends`); fetch the latest 10-K and extract Item 1A risk factors into section chunks | Requires a descriptive **`User-Agent`** header (SEC fair-access policy); no key; JSON + HTML endpoints; keep under ~10 req/s | **PRIMARY (live)** |
| **FRED** (Federal Reserve Bank of St. Louis) | Macroeconomic time series — policy rates (`FEDFUNDS`), 10-yr Treasury (`DGS10`), inflation (`CPIAUCSL`), unemployment (`UNRATE`), industrial production (`INDPRO`), real GDP (`GDPC1`) | Sector-aware **macro overlay** (`GET /macro`): maps the target's SEC sector to the most relevant series and returns ~5 years of observations with latest value + YoY change and a deterministic one-line read | **Keyless** — the `fredgraph.csv?id=<series>` graph endpoint (no API key); simple GET; ~10 req total per overlay | **LIVE (no key)** |
| **USAspending.gov** | Federal **contract** award history (award amounts, awarding agency/sub-agency, description, period of performance) | **GovCon profile** (`GET/POST /govcon`): total obligations + award count, **agency concentration** (top agency's share), **recompete exposure** (top awards with a PoP end within ~24 months), and an incumbent view; folds into risk findings via `govcon_flags` | **Keyless** REST API (`api.usaspending.gov/api/v2`); POSTs `spending_by_category` / `spending_by_award_count` / `spending_by_award`; contract award types A–D (excludes IDV ceilings) | **LIVE (no key)** |
| **SEC Financial Statement Data Sets** | Bulk, standardized quarterly financial-statement datasets (numbers as reported across filers) | Cross-check / backfill line items where the company-facts API is thin; build peer statistics | Bulk ZIP downloads; heavier, batch-oriented; no key | Extension |
| **OpenFIGI** (Bloomberg, open) | Security identifier mapping (ticker ↔ FIGI ↔ other IDs) | Normalize/disambiguate tickers when assembling a comps set from mixed identifiers | Free API key for higher rate limits; POST mapping jobs | Extension |
| **GDELT** | Global public news / media event and tone signal, open dataset | **News signals** (`GET /news`): `news_service` phrase-matches the target name against the GDELT DOC 2.0 API and returns recent articles, **labeled unverified media** (real, but not evidence-grade) and deliberately kept out of the evidence table | **Keyless** — the `api.gdeltproject.org` DOC 2.0 endpoint (no API key); best-effort (degrades to a source-error note on a bad response) | **LIVE (no key)** |
| **SAM.gov** | Federal opportunity and entity registration data (GovCon) | Would extend the live GovCon workstream with contract-vehicle and open-opportunity context | Free API key; the GovCon workstream is off for purely commercial targets | Extension |

---

## How the primary source works

### SEC EDGAR — the live backbone

EDGAR is the backbone of the entire flow. Four endpoint families do the work (all centralized in
`services/edgar_client.py`):

- **Ticker → CIK map** (`https://www.sec.gov/files/company_tickers.json`) — the full ticker→CIK
  directory, cached in-process. DealLens uppercases the user-entered ticker and looks it up; an unknown
  ticker raises an error the API surfaces as a **404**. The same map powers `/api/sec/search`.
- **Submissions** (`https://data.sec.gov/submissions/CIK{##########}.json`) — the filing history: form
  types, filing/report dates, accession numbers, and primary-document names, plus the company `name` and
  `sicDescription` (used as the target's sector). DealLens filters to 10-K / 10-Q / 8-K and takes the
  most recent up to a limit.
- **Company facts** (`https://data.sec.gov/api/xbrl/companyfacts/CIK{##########}.json`) — standardized
  XBRL concepts. `sec_financials.extract_financials` maps these (with fallbacks) to revenue, gross
  profit, operating income, net income, R&D, cash, and debt, and computes growth, margins, R&D %, and
  Rule-of-40. Each derived figure keeps a reference to its underlying XBRL point (concept, period end,
  accession, form) so it can be cited as evidence.
- **Archives** (`https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}`) — the primary
  filing document. DealLens fetches the latest 10-K's HTML, converts it to text with BeautifulSoup, and
  runs the heuristic section extractor to pull Item 1A (Risk Factors) for the risk scanner.

When a value is unavailable from company facts, it is **omitted** (left `null`) — never fabricated. See
[`docs/sec-ingestion.md`](./sec-ingestion.md) for the full ingestion path, XBRL concept mapping and
fallbacks, section extraction, and the `User-Agent` requirement.

### Real peers, real XBRL

Comparable companies are added **by ticker** (`POST /api/workspaces/{id}/comps {tickers:[...]}`) and
their financials come from the **same** company-facts pipeline as the target — real XBRL, not sample
values. The benchmark compares SEC-reported fundamentals (revenue, growth, gross/operating/net margin,
R&D %, Rule-of-40). Valuation-multiple columns exist in the schema but are always `null` (see below).

### Multi-year trends from the same XBRL

At ingestion, `sec_financials.extract_trends` reads the same company-facts document to build a
**multi-year** view: it groups the annual XBRL periods by fiscal year and, for the last five, reports
revenue plus gross/operating/net margin and R&D %, then computes a **revenue CAGR** over the span. This is
stored in `target.financials["trends"]` and served by `GET /api/workspaces/{id}/trends`
(`financial_benchmark_service.get_trends`). The CAGR is written as a `calculation` Evidence row and shown
as a "Revenue CAGR (N-yr)" line in the IC memo's financial table. See
[`docs/govcon-and-macro.md`](./govcon-and-macro.md).

---

## Operating notes

- **SEC fair access — `User-Agent` is mandatory.** SEC requires a descriptive `User-Agent` (identifying
  the app and a contact) on every request to `www.sec.gov` / `data.sec.gov`. DealLens sends the
  configured `SEC_USER_AGENT` and stays well under the ~10 req/s guidance. Requests without a descriptive
  UA may be throttled or blocked.

  ```
  SEC_USER_AGENT="DealLens Diligence Lab (portfolio project) you@example.com"
  ```

- **No key required for the three live sources.** EDGAR, **FRED** (via the `fredgraph.csv` graph
  endpoint), and **USAspending** all work with **no API key** — the live features need nothing beyond the
  SEC `User-Agent`. GDELT is also keyless; only OpenFIGI and SAM.gov (remaining extensions) use free API
  keys that live in environment variables (never committed).
- **Market/valuation data is omitted, not invented.** `market_cap`, `enterprise_value`, and
  `ev_revenue_multiple` are always `null`; there is no free source for them and the project does not
  fabricate valuation. The benchmark and memo say so explicitly.
- **Fiscal-year alignment is directional.** Peers may have different fiscal-year ends; comparisons are
  labeled directional rather than precise.
- **Terms of use.** Public data remains subject to the terms of its original providers. DealLens is an
  independent, non-commercial portfolio project and is not affiliated with, endorsed by, or sponsored by
  the SEC, the Federal Reserve, Bloomberg/OpenFIGI, GDELT, or any government agency or data vendor.

---

## Primary vs. extension summary

```
PRIMARY (live, drives the whole flow)
  └─ SEC EDGAR                                                    [no key, User-Agent required]
       ├─ company_tickers.json   (ticker → CIK, company search)
       ├─ submissions            (10-K / 10-Q / 8-K metadata, sector)
       ├─ companyfacts (XBRL)    (real target & peer financials + multi-year trends / revenue CAGR)
       └─ Archives               (10-K primary document → risk factors)

LIVE EXTENSIONS (implemented end to end; NO key required)
  ├─ FRED           (macro overlay via fredgraph CSV; sector → relevant series; GET /macro)
  ├─ USAspending    (federal contract awards → agency concentration + recompete; GET/POST /govcon)
  └─ GDELT          (keyless news signals via api.gdeltproject.org; GET /news; unverified media, off-evidence)

OMITTED (no free source; never fabricated)
  └─ Market valuation multiples  (market cap, enterprise value, EV/Revenue)

Remaining extension points (interfaces wired; not required for the core flow)
  ├─ SEC Financial Statement Data Sets   (bulk financial backfill / peer stats)
  ├─ OpenFIGI                            (identifier normalization for comps)
  └─ SAM.gov                             (federal opportunity / entity context, extends GovCon)
```
