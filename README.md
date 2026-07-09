# DealLens Diligence Lab

**A public-data AI diligence copilot for investment research, red-flag detection, and IC memo generation.**

DealLens Diligence Lab is an independent, non-commercial portfolio project that demonstrates how an
investment team might use AI to accelerate *first-pass* diligence on a **real public company**. You enter
a **ticker**; it resolves the company against SEC EDGAR, pulls XBRL financials and recent filings,
extracts the latest 10-K's risk factors, surfaces risks and red flags, benchmarks against real public
peers, generates diligence questions, drafts an investment committee (IC) memo, and red-teams the thesis
— while keeping **every material claim source-grounded, traceable, and reviewable by a human**.

> The point is not to automate investment judgment. The point is to show how AI can accelerate the
> evidence-gathering, issue-spotting, memo-drafting, and red-team process while keeping humans
> accountable for decisions.

---

## ⚠️ Disclaimer

DealLens Diligence Lab is an independent, non-commercial portfolio project using public data (primarily
SEC EDGAR). It is not affiliated with, endorsed by, or sponsored by any investment firm, private equity
firm, public company, data vendor, or AI platform vendor. Outputs are AI-assisted, deterministic drafts
for educational and demonstration purposes only, are **not investment advice**, and should not be used to
make investment decisions. Qualitative risk severities are heuristic and require human validation;
market/valuation data is omitted.

---

## The demo in one minute

Enter a public-company **ticker** (e.g. **MSFT**, **NVDA**, **CRWD**) and get a **real, SEC-grounded
diligence pack**. On creation the backend resolves the ticker against SEC EDGAR, pulls XBRL company facts,
lists recent 10-K / 10-Q / 8-K filings, fetches the latest 10-K and extracts its risk factors, then
deterministically builds the whole pack:

> **Example — CRWD (CrowdStrike):** ~$4.8B revenue, ~22% growth, ~75% gross margin, a **negative GAAP
> operating margin**, and a Rule-of-40 around 16% — benchmarked against real peers (PANW, ZS, S). Red
> flags come from the real 10-K (legal/regulatory, AI-disruption, integration/M&A) plus deterministic
> financial flags (GAAP operating loss, net loss).

The app produces a full first-pass **diligence pack**: target overview (real XBRL), real public comps and
a fundamentals benchmark, diligence plan, red-flag matrix, questions by workstream, a draft IC memo, a
bear-case memo, and an inspectable **evidence table** where every claim traces to an XBRL concept or a
10-K passage on `sec.gov`.

Market **valuation multiples are omitted** (no free source), and qualitative severities are keyword-based
heuristics that require human validation. Creating a workspace with a ticker requires **network access**
to SEC EDGAR and a descriptive `SEC_USER_AGENT`.

---

## Quickstart

### Option A — Docker (production-shaped: Postgres + pgvector)

```bash
cp .env.example .env
docker compose up --build
# web  → http://localhost:3000
# api  → http://localhost:8000  (docs at /docs)
```

### Option B — Local (no Docker; SQLite, zero external services)

The backend defaults to SQLite (a local file) and requires no database server and no LLM key. Seeding
real demo workspaces requires **network access** to SEC EDGAR and a descriptive `SEC_USER_AGENT`.

**Backend (Python 3.11+):**

```bash
cd apps/api
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux:        source .venv/bin/activate
pip install -e ".[dev]"
export SEC_USER_AGENT="DealLens Diligence Lab (portfolio) you@example.com"   # SEC fair-access
python -m src.seed.load_seed          # seed real demo workspaces (MSFT, CRWD) from live SEC — needs network
uvicorn src.main:app --reload         # http://localhost:8000
```

`load_seed` ingests each demo ticker and its peers from live SEC EDGAR and runs the full analysis; it is
a no-op if workspaces already exist. You can also create workspaces from the UI by entering a ticker.

**Frontend (Node 18+):**

```bash
cd apps/web
npm install
npm run dev                           # http://localhost:3000
```

Set `NEXT_PUBLIC_API_URL=http://localhost:8000` in `apps/web/.env.local` if your API is elsewhere.

### Makefile shortcuts

```bash
make install   # install api + web deps
make seed      # seed real demo workspaces (MSFT, CRWD) from live SEC (needs network)
make dev       # run api + web together (needs two terminals or a process manager)
make test      # run backend pytest suite (live SEC tests skip when EDGAR is unreachable)
make up        # docker compose up --build
make down      # docker compose down
```

---

## The diligence workflow

```
Ticker ─▶ SEC EDGAR ingest (XBRL + latest 10-K) ─▶ Target + risk-factor chunks
    │                                                        │
    ├─ Financial benchmark ◀── Public comps (real peers by ticker) ◀──┤
    ├─ Risk / red-flag matrix ◀──────────────────────────────┤
    ├─ Diligence questions (by workstream) ◀─────────────────┤
    ├─ IC memo draft ◀───────────────────────────────────────┤
    ├─ Bear-case / red-team memo ◀───────────────────────────┤
    └─ Evidence & audit table (every material claim) ◀────────┘
```

Creating a workspace with a ticker runs the whole pipeline in one pass. Every generated artifact links
back to **Evidence** rows that record the claim, its type (`fact` / `calculation` / `inference` /
`assumption`), the SEC source (XBRL concept or 10-K passage with its `sec.gov` URL), and confidence. See
[`docs/evidence-model.md`](docs/evidence-model.md).

### Roadmap extensions (live)

Three keyless, real-data features now sit on top of the SEC core flow — **no API key required for either
USAspending or FRED**. Full details in [`docs/govcon-and-macro.md`](docs/govcon-and-macro.md).

- **Multi-year XBRL trends** (`GET /trends`) — revenue + gross/operating/net margin and R&D % for the last
  five fiscal years plus a computed **revenue CAGR**, from the same SEC company facts; the CAGR is cited as
  a `calculation` and shown as an IC-memo row.
- **FRED macro overlay** (`GET /macro`, **keyless**) — a sector-aware macro backdrop (policy rate, 10-yr
  yield, inflation, unemployment, industrial production, GDP) mapped from the target's SEC sector, with
  latest value + YoY. Context, not a forecast.
- **GovCon federal-contract diligence** (`GET`/`POST /govcon`, **keyless**, via USAspending.gov) —
  **agency concentration** (top agency's share of obligations), **recompete** exposure (top awards with a
  period of performance ending within ~24 months), top awards, and an incumbent view. `POST` re-runs the
  analysis so real `govcon_risk` findings and a memo GovCon section fold in. This makes DealLens suit
  **defense / GovCon diligence** — e.g. Leidos shows ~$128B in federal obligations with DoD at ~52%
  concentration.

## Architecture

| Layer            | Choice                                                              |
|------------------|---------------------------------------------------------------------|
| Frontend         | Next.js (App Router), TypeScript, Tailwind CSS, Recharts            |
| Backend          | FastAPI, Python 3.11+                                                |
| ORM / DB         | SQLAlchemy 2.0 — SQLite by default, PostgreSQL + pgvector in Docker  |
| Data source      | **SEC EDGAR** — ticker→CIK, submissions, companyfacts (XBRL), 10-K docs |
| Retrieval        | Deterministic keyword/TF over 10-K section chunks (pgvector-ready)   |
| LLM layer        | Deterministic engine by default; optional live LLM only re-voices prose |
| Tests            | pytest — incl. a "no uncited material claims" check on a real SEC workspace |

Details: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/data-sources.md`](docs/data-sources.md) ·
[`docs/diligence-methodology.md`](docs/diligence-methodology.md) ·
[`docs/govcon-and-macro.md`](docs/govcon-and-macro.md) ·
[`docs/sec-ingestion.md`](docs/sec-ingestion.md) ·
[`docs/evidence-model.md`](docs/evidence-model.md) ·
[`docs/example-case-study.md`](docs/example-case-study.md) ·
[`docs/demo-script.md`](docs/demo-script.md) ·
[`docs/disclaimers.md`](docs/disclaimers.md)

## Public data sources

| Source                              | Use                                                       | Status |
|-------------------------------------|-----------------------------------------------------------|--------|
| SEC EDGAR APIs                      | Ticker→CIK, submissions (10-K/10-Q/8-K), company facts (XBRL + multi-year trends), 10-K documents | **Primary (live)** |
| FRED                                | Sector-aware macro overlay (rates, inflation, unemployment, industrial production, GDP) | **Live (no key)** |
| USAspending.gov                     | GovCon: federal contract awards → agency concentration + recompete | **Live (no key)** |
| SEC Financial Statement Data Sets   | Standardized financial statement data                     | Extension |
| OpenFIGI                            | Security identifier mapping                                | Extension |
| GDELT                               | Public news / media signal discovery                      | Extension |
| SAM.gov                             | Federal opportunity / entity context (extends GovCon)     | Extension |

The core flow runs on **SEC EDGAR** (no key; a descriptive `SEC_USER_AGENT` is required). **FRED** and
**USAspending** are live and **need no key**; OpenFIGI, GDELT, and SAM.gov remain wired extension points.
Market **valuation multiples are omitted** — no free source. See
[`docs/data-sources.md`](docs/data-sources.md) and [`docs/govcon-and-macro.md`](docs/govcon-and-macro.md).

## Design principles

1. Every material claim is tied to real evidence — an XBRL concept or a 10-K passage — where possible.
2. Facts, calculations, inferences, and assumptions are labeled distinctly.
3. Outputs are never presented as investment advice.
4. Citations are never fabricated — every `EV-###` cited must resolve.
5. Missing financials are omitted, not invented; **market/valuation multiples are omitted entirely** (no
   free source).
6. Qualitative risk severities are heuristic (keyword-based) and flagged as requiring human validation.
7. LLMs may draft/re-voice narrative, but calculations are deterministic and auditable.

## Repository layout

```
apps/web    Next.js frontend (pages, components, api client)
apps/api    FastAPI backend (models, schemas, routers, services, agents, seed data)
docs        Architecture, methodology, evidence model, data sources, demo script
```

## License

MIT — see [`LICENSE`](LICENSE). Public data remains subject to the terms of its original providers.
