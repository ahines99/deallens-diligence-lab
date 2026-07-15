# Architecture

DealLens Diligence Lab is a two-app system: a **Next.js frontend** (`apps/web`) that renders diligence
artifacts, and a **FastAPI backend** (`apps/api`) that owns the data model, the real SEC EDGAR ingestion
pipeline, the deterministic analysis engine, the optional LLM abstraction, and the evidence/audit layer.
The [`docs/CONTRACTS.md`](./CONTRACTS.md) file is the single source of truth for the HTTP shapes both
sides share; TypeScript mirrors live in `apps/web/src/lib/types.ts`.

The original public-company path remains fully live and ticker-driven. You create a workspace with a
ticker (e.g. `CRWD`); the backend resolves it against SEC EDGAR, pulls XBRL financials and recent
filings, extracts the latest 10-K's risk factors, and deterministically builds the whole pack —
evidence, risks, questions, plan, IC memo, and bear case — every material claim cited to a real SEC
source. Private deals instead use versioned management financial imports and data-room documents, then
flow through deterministic underwriting, deal execution, approved claims, and frozen IC packet versions.
See [`WAVE3.md`](./WAVE3.md) for the institutional architecture.

The system runs in two postures without code changes:

- **Default posture** — SQLite (a local file), the deterministic analysis engine, no LLM key required.
  Reproducible from live SEC data. This is the posture the demo runs in.
- **Production-shaped posture** — Postgres + pgvector via Docker Compose, plus an optional live LLM that
  only *re-voices* the already-grounded prose (never changes a number). Same code paths, different
  adapters.

> **Network note.** Because the primary flow is real, creating a ticker-driven workspace requires network
> access to SEC EDGAR (`www.sec.gov` / `data.sec.gov`) and a descriptive `SEC_USER_AGENT`. Unit tests run
> offline; live integration tests are skipped when EDGAR is unreachable.

---

## Component diagram

```
                          ┌───────────────────────────────────────────────┐
                          │                 apps/web  (Next.js 15)          │
                          │              App Router · TS · Tailwind         │
                          │                                                 │
    Browser ───────────▶  │  Server Components ──▶ lib/api.ts (typed client)│
                          │   /workspaces/[id]/{overview,target,filings,    │
                          │    comps,risks,questions,memo,red-team,evidence}│
                          │  Client Components: GenerateButton, Recharts,   │
                          │   ClaimBadge, SourceCitation, MemoViewer        │
                          └───────────────────────┬─────────────────────────┘
                                                  │  HTTP  (JSON, /api/*)
                                                  │  NEXT_PUBLIC_API_URL
                          ┌───────────────────────▼─────────────────────────┐
                          │                 apps/api  (FastAPI)              │
                          │                                                  │
                          │  routers/   ── HTTP layer, request/response shapes│
                          │      │                                            │
                          │  routers/ … financials (trends+macro) · govcon    │
                          │  services/  ── orchestration                      │
                          │   workspace_service (create + ingest on ticker)   │
                          │   sec_ingestion_service · analysis_service        │
                          │   financial_benchmark_service (.get_trends)       │
                          │   evidence_service · govcon_service               │
                          │      │                                            │
                          │      ├──────── SEC layer (services/) ───────────┐ │
                          │      │   edgar_client   (ticker→CIK, submissions,│ │
                          │      │                   companyfacts, doc fetch)│ │
                          │      │   sec_financials (XBRL concept mapping    │ │
                          │      │                   + .extract_trends)      │ │
                          │      │   filing_sections(Item 1/1A/7 extraction) │ │
                          │      │   fred_service · usaspending_service      │ │
                          │      │     (keyless macro / federal awards)      │ │
                          │      │                                           │ │
                          │      ├──────── agents/ ─────────────────────────┐│ │
                          │      │   financial_analyst · risk_analyst        ││ │
                          │      │   diligence_lead · ic_memo_writer         ││ │
                          │      │   red_team_reviewer · citation_auditor    ││ │
                          │      │   llm_provider (optional live re-voice)   ││ │
                          │      │                                           ▲│ │
                          │      ▼                                           ││ │
                          │  evidence_service ── Evidence rows (EV-###) ─────┘│ │
                          │  models/ (SQLAlchemy 2.0) ────────────────────────┘ │
                          └──────────┬──────────────────────────┬──────────────┘
                                     │                          │
                          ┌──────────▼─────────┐    ┌───────────▼──────────┐
                          │ SQLite (default)   │    │ Postgres + pgvector  │
                          │ file, zero-config  │ OR │ (docker compose)     │
                          └────────────────────┘    └──────────────────────┘
                                     ▲
                          ┌──────────┴────────────────────────────────────┐
                          │ SEC EDGAR (PRIMARY, live, no key)              │
                          │  company_tickers.json · submissions ·         │
                          │  companyfacts (XBRL) · Archives (10-K docs)    │
                          └───────────────────────────────────────────────┘
                                     ▲ (live, keyless real-data extensions)
                          ┌──────────┴────────────────────────────────────┐
                          │ FRED (fredgraph CSV → macro overlay)          │
                          │ USAspending.gov (federal awards → GovCon)     │
                          └───────────────────────────────────────────────┘
                                     ▲ (remaining wired extension points)
                          ┌──────────┴────────────────────────────┐
                          │ OpenFIGI · GDELT · SAM.gov             │
                          └───────────────────────────────────────┘
```

Reference data (`seed/risk_taxonomy.json`, `seed/diligence_question_templates.json`) feeds the
deterministic engine; `seed/load_seed.py` seeds real demo workspaces (MSFT, CRWD) from live SEC.

---

## The two apps

### `apps/web` — Next.js 15 (App Router)

- **Server Components fetch and render.** Every page under `/workspaces/[workspaceId]/*` is an async
  React Server Component that calls the typed `api` client (`@/lib/api`) and renders the result. There
  is no client-side data fetching in `useEffect`; the server does the fetch and streams HTML.
- **Client Components are for interaction only** — Recharts, `react-markdown`, and any `onClick` /
  `useRouter` code carry `"use client"`. Creating a workspace with a ticker and adding peer tickers are
  the main mutation surfaces.
- **Evidence is first-class in the UI.** Material claims render a `ClaimBadge`
  (fact / calculation / inference / assumption) and a `SourceCitation` that resolves `EV-###` to the
  evidence row (with its real `sec.gov` source URL) on the Evidence & Audit page.

### `apps/api` — FastAPI (Python 3.11+)

Layered so that HTTP, orchestration, the SEC pipeline, generation, and persistence are separable:

| Layer                  | Responsibility                                                                            |
|------------------------|-------------------------------------------------------------------------------------------|
| `routers/`             | HTTP endpoints; validate/serialize against the CONTRACTS shapes. Includes `financials` (`/trends` + `/macro`) and `govcon` (`GET`/`POST /govcon`) |
| `services/` (orchestr.)| `workspace_service`, `analysis_service`, `sec_ingestion_service`, `financial_benchmark_service` (incl. `.get_trends`), `evidence_service`, `govcon_service` |
| `services/` (data layer)| `edgar_client`, `sec_financials` (incl. `.extract_trends`), `filing_sections` — EDGAR access + parsing; `fred_service` (keyless macro), `usaspending_service` (keyless federal awards) |
| `agents/`              | Deterministic producers: `financial_analyst`, `risk_analyst` (incl. `.govcon_flags`), `diligence_lead`, `ic_memo_writer`, `red_team_reviewer`, `citation_auditor`; plus `llm_provider` |
| `models/`              | SQLAlchemy 2.0 ORM (`Target`, `Filing`, `DocumentChunk`, `ComparableCompany`, `Evidence`, `RiskFinding`, `GovConProfile`, …); SQLite or Postgres |
| `seed/`                | Reference data (risk taxonomy, question templates) + `load_seed.py` (real demo workspaces) |

All external network access is centralized in `services/edgar_client.py`, which keeps the rest of the
services testable.

---

## Request flow — creating a ticker-driven workspace

Creating a workspace with a ticker is the representative path; it runs the whole pipeline end to end:

```
1. User submits New workspace with ticker="CRWD"            (apps/web → typed api client)
2. POST /api/workspaces {ticker,name?,deal_type,investment_question?}   (FastAPI router)
3. workspace_service.create_workspace:
     a. edgar_client.resolve_ticker("CRWD")
          → company_tickers.json → {cik, ticker, name}   (unknown ticker → EdgarError → 404)
     b. default name + investment_question from the resolved company
     c. persist Workspace (status="draft")
4. sec_ingestion_service.ingest_company:
     a. get_submissions(cik)            → sector (sicDescription) + recent filings (10-K/10-Q/8-K)
     b. get_company_facts(cik)          → XBRL company facts
        sec_financials.extract_financials → revenue, growth, margins, R&D%, Rule-of-40, cash, debt
                                            (each with its source XBRL point: concept/end/accession/form)
        sec_financials.extract_trends     → last-5-FY revenue/margins + revenue CAGR
                                            (stored in target.financials["trends"])
     c. fetch latest 10-K primary document (Archives) → BeautifulSoup → text
        filing_sections.extract_sections → Item 1 / 1A / 7 (largest-span heuristic)
        split_paragraphs → DocumentChunk rows (section-labelled, source_url set)
     d. upsert Target (is_synthetic=false, data_source="SEC EDGAR (XBRL + 10-K)")
5. analysis_service.run_full_analysis (rebuilds current views and seals a new immutable run/artifact):
     • financial evidence  (facts + calculations from XBRL)           → Evidence (source_type "xbrl")
     • risk findings       (taxonomy keyword scan of 10-K risk factors → Evidence "sec_filing")
                           (+ deterministic financial-metric flags     → Evidence "xbrl")
     • plan + questions    (diligence_lead over the findings)
     • benchmark           (if peers present)
     • IC memo             (ic_memo_writer → optional llm re-voice)
     • bear case / red-team(red_team_reviewer → optional llm re-voice)
6. Router returns Workspace; every /workspaces/[id]/* page re-fetches and renders real data.
```

Adding peer tickers (`POST /api/workspaces/{id}/comps {tickers:[...]}`) fetches each peer's real XBRL,
then **re-runs `run_full_analysis`** so the benchmark and memo reflect the new peer set.
`POST /api/sec/ingest` re-runs the same ingestion for an existing workspace.

**Real-data overlays** (keyless; see [`docs/govcon-and-macro.md`](./govcon-and-macro.md)):

- `GET /api/workspaces/{id}/trends` → `financial_benchmark_service.get_trends` reads the multi-year trend
  already stored in `target.financials["trends"]` (no extra network call); the revenue CAGR is also
  written as a `calculation` Evidence row during analysis and shown in the memo.
- `GET /api/workspaces/{id}/macro` → the `financials` router calls `fred_service.macro_for_sector` on the
  target's SEC sector, fetching the relevant series from the keyless `fredgraph` CSV.
- `POST /api/workspaces/{id}/govcon` → `govcon_service.fetch` pulls the recipient's federal awards via
  `usaspending_service` (keyless), stores a `GovConProfile`, then **re-runs
  `analysis_service.run_full_analysis`** so `risk_analyst.govcon_flags` and the memo's GovCon section fold
  in. An upstream USAspending failure surfaces as a **502**.

Key contract properties (from `CONTRACTS.md`):

- Unknown ticker → **404**; an EDGAR network failure → **502**.
- `generate` endpoints rebuild the current artifact from the target's real data while retaining prior
  evidence and sealing a new `AnalysisRun`/`ArtifactVersion`. `GET` before generate returns **404**.
- Generating risks / questions / memo / red-team **also creates the Evidence rows they cite**, so the
  audit trail is never out of sync with the narrative.

---

## The analysis engine is deterministic; the LLM only polishes

`analysis_service.run_full_analysis` assembles every artifact **in code** from the target's real XBRL
values and 10-K text. Numbers are never asked of a model:

- **Financials** (revenue, growth, margins, R&D %, Rule-of-40, cash, debt) are computed in
  `sec_financials` from XBRL company facts and rounded deterministically (`claim_type: calculation` for
  ratios/growth, `fact` for reported line items).
- **Risk findings** come from a keyword/TF scan of the 10-K risk-factor chunks against the taxonomy, plus
  deterministic financial-metric flags. Severities are heuristic, computed in code.
- **Memos** are drafted deterministically by `ic_memo_writer` / `red_team_reviewer` with every figure
  tagged to an `EV-###`.

The optional live LLM (`agents/llm_provider.py`, active only when `LLM_MODE=live` and `LLM_API_KEY` is
set) runs `polish_markdown` over the finished memo: it improves flow but is instructed to **change no
number, fact, or citation** and to keep every `[EV-###]` tag in place. Any failure falls back to the
deterministic text. This is the same design rule in both postures: **the model may draft narrative, but
calculations are deterministic and auditable.**

---

## SQLite vs. Postgres + pgvector

One set of SQLAlchemy 2.0 models runs against either database:

| Concern    | SQLite (default)                     | Postgres + pgvector (Docker)                          |
|------------|--------------------------------------|-------------------------------------------------------|
| Setup      | Zero external services; a file        | `docker compose up --build`                           |
| Use        | Local dev, the demo, CI               | Production-shaped deployment                           |
| Retrieval  | Keyword/TF over `DocumentChunk` text  | Same interface; pgvector column ready for embeddings   |
| Migrations | Alembic; legacy `create_all` upgrade  | Alembic on container startup                           |

`DATABASE_URL` selects the backend (`sqlite:///./data/deallens.sqlite3` by default). Retrieval over the
10-K chunks is deterministic keyword/TF today; swapping in a pgvector similarity search is a data-layer
change, not an application-layer one.

Outbound integrations use the same database as a transactional outbox. Workflow mutations, immutable
audit events, and queued webhook deliveries commit atomically. A separate polling worker claims due
deliveries, validates the destination, decrypts the signing secret only in memory, sends a canonical
HMAC-signed body without redirects, and persists the response/retry state. Consumers can safely
deduplicate retries using the immutable delivery ID.

---

## Retrieval design

The latest 10-K is **chunked by section** at ingestion time. `filing_sections.extract_sections` locates
Item 1 (Business), Item 1A (Risk Factors), and Item 7 (MD&A) with a *largest-span* heuristic (see
[`docs/sec-ingestion.md`](./sec-ingestion.md)); `split_paragraphs` breaks each section into
200–1600-char chunks. Each `DocumentChunk` keeps its `section` label and the filing's `source_url`, so
every Evidence snippet traces back to the exact section of the exact filing.

- **Deterministic retriever.** The risk scanner (`agents/risk_analyst.py`) walks the
  `risk_taxonomy.json` signal phrases (e.g. `concentration`, `litigation`, `artificial intelligence`)
  against the risk-factor + MD&A chunks and scores by matched-signal count and term frequency; the
  highest-scoring chunk per category seeds a finding with the most on-topic sentence quoted as evidence.
- **pgvector-ready.** The same section chunks can be indexed by an embedding store with no change above
  the retrieval boundary. The MVP ships the deterministic scanner because it is reproducible and makes
  the evidence trail explainable — you can see exactly which section matched which taxonomy signal.

Two retrieval/generation paths now build on the same chunks. **Cited filings Q&A** (`POST /qa`,
`services/filings_qa_service.py`) runs a deterministic **BM25** rank over the ingested filing sections and
returns a strictly extractive, quoted answer — or an explicit abstention when no section clears the bar —
so the answer never drifts from the source text. The **memo faithfulness report** (`GET /memo/faithfulness`)
re-verifies every memo on demand: it counts citations, flags unresolved `EV-###` refs, and lists uncited
numeric sentences. Long-running work (workspace builds) has also moved off in-process background tasks onto
a **durable, DB-backed job queue** (`models/job.py`, `services/job_service.py`, worker in `workers/jobs.py`)
with at-least-once claiming, retries, heartbeats, and stale-claim recovery — the same transactional-outbox
pattern as the webhook worker.

---

## Where the evidence / audit layer sits

The evidence layer sits **between generation and persistence**, not as a reporting afterthought.
`evidence_service` allocates a stable `EV-###` ref (sequentially per workspace) and writes an Evidence
row for every material claim as the artifact that cites it is generated. XBRL facts and calculations
carry `source_type: "xbrl"`; 10-K risk-factor snippets carry `source_type: "sec_filing"` with a real
`sec.gov` `source_url`. Risk findings, questions, and memo passages reference these refs, so the
Evidence & Audit page is a complete, inspectable index of what the pack asserts and why.

The `citation_auditor` enforces the **"no uncited material claims"** rule — every `EV-###` a memo cites
must resolve to a real evidence row — and the backend test suite runs this as
`tests/test_no_uncited_material_claims.py` against a **real** MSFT workspace (skipped when SEC is
unreachable). See [`docs/evidence-model.md`](./evidence-model.md).

---

## Related docs

[`data-sources.md`](./data-sources.md) ·
[`diligence-methodology.md`](./diligence-methodology.md) ·
[`evidence-model.md`](./evidence-model.md) ·
[`govcon-and-macro.md`](./govcon-and-macro.md) ·
[`sec-ingestion.md`](./sec-ingestion.md) ·
[`example-case-study.md`](./example-case-study.md) ·
[`demo-script.md`](./demo-script.md) ·
[`disclaimers.md`](./disclaimers.md)
