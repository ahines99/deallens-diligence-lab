# SEC ingestion

This is the **real, implemented** pipeline that turns a ticker into a source-grounded diligence pack. It
is not a stub. When you create a workspace with a ticker (or call `POST /api/sec/ingest`), the backend
resolves the ticker against SEC EDGAR, pulls XBRL financials and recent filings, fetches the latest 10-K,
and extracts its risk factors — all with real `sec.gov` source URLs. This document describes exactly what
each step does, the endpoints it hits, the XBRL concept mapping, the section-extraction heuristic and its
limitations, and the fair-access `User-Agent` requirement.

All EDGAR network access lives in `apps/api/src/services/edgar_client.py`. The financial mapping lives in
`sec_financials.py`, section extraction in `filing_sections.py`, and orchestration in
`sec_ingestion_service.py`.

---

## The contract surface

| Method | Path | Body → Returns |
|---|---|---|
| POST | `/api/workspaces` | `{ ticker?, name?, deal_type, investment_question? }` → `Workspace` — a `ticker` triggers ingestion + full analysis (unknown ticker → 404, network failure → 502) |
| GET | `/api/sec/search?q=` | → `[{ "cik", "ticker", "name" }]` — ticker/name search over the SEC company list |
| POST | `/api/sec/ingest` | `{ workspace_id, ticker?, cik?, form_types?: [string], limit?: int }` → `Filing[]` — re-ingests an existing workspace and re-runs analysis |
| POST | `/api/workspaces/{id}/comps` | `{ tickers?: [string] }` → `ComparableCompany[]` — each peer fetched from the same XBRL pipeline |

A `Filing` records `company_name`, `ticker`, `cik`, `form_type`, `filing_date`, `accession_number`,
`document_url`, a `section_count`, and `is_synthetic` (always `false` for real filings). The `/filings`
page renders the resulting table.

---

## The pipeline, step by step

### 1. Ticker → CIK

EDGAR keys everything on a 10-digit, zero-padded **CIK** (Central Index Key), not the ticker.
`edgar_client.resolve_ticker` normalizes the input to uppercase and looks it up in the ticker map loaded
(and cached in-process) from:

```
https://www.sec.gov/files/company_tickers.json
```

The map yields `{ cik (10-digit, zero-padded), ticker, name }`. An unknown ticker raises `EdgarError`,
which the API surfaces as **404**. `search_companies` (behind `/api/sec/search`) reuses the same map,
ranking exact-ticker, prefix, then name-substring matches.

### 2. Submissions — pick the filings and the sector

With a CIK, `edgar_client.get_submissions` reads:

```
https://data.sec.gov/submissions/CIK{##########}.json
```

From `filings.recent` it pulls parallel arrays (`form`, `filingDate`, `reportDate`, `accessionNumber`,
`primaryDocument`) and builds a `FilingMeta` per filing, constructing each primary-document URL under
`Archives`. `recent_filings(cik, forms, limit)` filters to the requested forms (default `10-K`, `10-Q`,
`8-K`) and takes the most recent up to `limit` (default 8). The submission JSON's `name` becomes the
company name and `sicDescription` becomes the target's `sector`.

If no 10-K appears in that window, the ingester makes a second, 10-K-only `recent_filings` call so the
risk-factor extraction always has a filing to work from.

### 3. Company facts — XBRL financials with fallbacks

For financials, `edgar_client.get_company_facts` reads:

```
https://data.sec.gov/api/xbrl/companyfacts/CIK{##########}.json
```

`sec_financials.extract_financials` maps standardized `us-gaap` concepts to the fields DealLens reports.
Each concept has an **ordered fallback list** — the first concept with usable annual data wins:

| Field | Concepts tried (in order) |
|---|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues` → `RevenueFromContractWithCustomerIncludingAssessedTax` → `SalesRevenueNet` |
| Gross profit | `GrossProfit` (fallback: `Revenue − CostOfRevenue/CostOfGoodsAndServicesSold/CostOfGoodsSold`, only when the cost period matches revenue) |
| Operating income | `OperatingIncomeLoss` |
| Net income | `NetIncomeLoss` → `ProfitLoss` |
| R&D | `ResearchAndDevelopmentExpense` |
| Cash | `CashAndCashEquivalentsAtCarryingValue` → `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| Debt | `LongTermDebtNoncurrent` → `LongTermDebt` → `DebtLongtermAndShorttermCombinedAmount` |
| Assets | `Assets` |

**Annual de-duplication via `frame`.** XBRL company facts include many overlapping periods. DealLens
keeps only the clean **annual** points by matching the SEC `frame` label: duration concepts must match
`^CY\d{4}$` (a full calendar/fiscal year) and instant concepts (cash, debt, assets) must match
`^CY\d{4}Q4I$` (a year-end snapshot). Points are sorted oldest-first by period `end`. Revenue additionally
de-dupes by fiscal-year (keeping the latest point per year) so growth is computed from the two most
recent distinct fiscal years.

**Derived metrics** (all deterministic, rounded to 4 decimals):

- `revenue_growth = (revenue − prior_revenue) / prior_revenue`
- `gross_margin = gross_profit / revenue`, `operating_margin = operating_income / revenue`,
  `net_margin = net_income / revenue`, `rnd_pct = rnd / revenue`
- `rule_of_40 = revenue_growth + operating_margin`

`extract_financials` returns these values plus a `sources` map: for each metric, the underlying XBRL
point's `concept`, period `end`, `accession`, and `form`. That map is what lets every financial figure be
cited to a real filing on the Evidence & Audit page. Any metric whose concepts are all absent is left
`null` — never fabricated.

### 4. The 10-K document → text → sections

For the latest 10-K, `edgar_client.fetch_document_text` fetches the primary-document HTML (capped at
~8 MB), parses it with **BeautifulSoup** (`html.parser`, raw bytes so the declared charset is honored),
drops `<script>`/`<style>`, extracts text, normalizes non-breaking spaces, and collapses whitespace.

`filing_sections.extract_sections` then locates the three sections DealLens cares about — **Item 1
(Business)**, **Item 1A (Risk Factors)**, and **Item 7 (MD&A)**:

- 10-K text repeats item headers (once in the table of contents, once at the real section). For each
  section, the extractor finds every candidate start and every next-section boundary, then picks the
  candidate whose span to the next boundary is **largest** — the real body, not the short TOC entry.
- Boundary patterns use lookaheads so `Item 1` does not match `Item 1A`/`Item 10`, etc. A section is only
  emitted if its extracted body exceeds 400 characters.

> **Heuristic — and honest about it.** This is a text-pattern heuristic, not an XBRL-structured parse. It
> works reliably across mainstream 10-K formatting but can mis-bound sections in unusual layouts (exhibit
> incorporation, heavy tables, non-standard headers). It is deterministic and traceable — every chunk
> keeps the section label and source URL — but the qualitative risk read that follows should always be
> validated by a human against the filing.

### 5. Chunking for retrieval / evidence

`filing_sections.split_paragraphs` breaks each extracted section into paragraph-ish chunks (sentence-ish
splits accumulated into ~200–1600-character windows). Each chunk becomes a `DocumentChunk` row with its
`section` label (e.g. *Risk Factors (Item 1A)*), `chunk_index`, `chunk_text`, and the filing's
`source_url`. The filing's `section_count` records how many chunks were produced. These chunks are what
the risk scanner queries; because each carries its section and source URL, every resulting Evidence row
traces back to an exact passage of an exact filing.

### 6. Target upsert + full analysis

`sec_ingestion_service.ingest_company` upserts the `Target` (`target_type="public_company"`,
`is_synthetic=false`, `data_source="SEC EDGAR (XBRL + 10-K)"`), fills the financial fields and stores the
full `financials` dict (including the `sources` map), and derives a `description` from real prose
sentences in the Business section (skipping table-of-contents / page-number noise). The workspace-create
and `/api/sec/ingest` paths then call `analysis_service.run_full_analysis` to (re)build the whole pack.

```
ticker
  │  resolve_ticker → company_tickers.json
  ▼
CIK ──▶ submissions ──▶ Filing rows (10-K/10-Q/8-K, real accession + document_url)
  │        │
  │        └─ sicDescription → sector,  name → company name
  │
  ├──▶ companyfacts (XBRL) ──▶ extract_financials ──▶ revenue, growth, margins, R&D%,
  │                                                    Rule-of-40, cash, debt  (+ sources map)
  │
  └──▶ latest 10-K Archives doc ──▶ BeautifulSoup text ──▶ extract_sections (Item 1 / 1A / 7,
                                                            largest-span) ──▶ split_paragraphs
                                                            ──▶ DocumentChunk rows
                                                                  │
                                                                  ▼
                                            run_full_analysis: evidence · risks · questions ·
                                            plan · IC memo · bear case  (all EV-### cited)
```

---

## Form types

| Form | Contents | Diligence use |
|---|---|---|
| **10-K** | Annual report — business, risk factors, MD&A, financial statements | Richest source: Item 1A feeds the risk scanner; XBRL feeds the financial profile and benchmark |
| **10-Q** | Quarterly report — condensed financials, updates | Recency; quarter-over-quarter trend context |
| **8-K** | Material current events | Event-level signals (leadership change, litigation, material agreements) |

`form_types` is a request parameter on `/api/sec/ingest`, so a caller can widen or narrow the set;
financials and risk-factor extraction key off the **10-K** and the XBRL company facts.

---

## Fair access — the `User-Agent` requirement

SEC's fair-access policy **requires a descriptive `User-Agent` header** identifying the application and a
contact on every request to `www.sec.gov` / `data.sec.gov`. Requests without one are throttled or
blocked. DealLens sends the configured `SEC_USER_AGENT` (with `Accept-Encoding: gzip, deflate`) and stays
well under the ~10 req/s guidance (`edgar_client.polite_pause` is available as a courtesy throttle).

```
SEC_USER_AGENT="DealLens Diligence Lab (portfolio project) you@example.com"
```

Because ingestion is **real**, it needs network access. On an EDGAR failure the create/ingest endpoints
return **502** (or **404** for an unknown ticker) rather than silently substituting fake data. There is
no synthetic fallback in the primary flow.
