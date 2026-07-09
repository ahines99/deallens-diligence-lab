# Demo script

A step-by-step runbook for recording the DealLens Diligence Lab walkthrough. It is **ticker-driven and
real**: you enter a public-company ticker (e.g. **CRWD**) and the backend pulls live SEC EDGAR data —
XBRL financials, recent filings, and the latest 10-K's risk factors — then deterministically builds the
whole pack. Because it is real, the demo **needs network access** to SEC EDGAR and a descriptive
`SEC_USER_AGENT`.

**Target length:** 5–7 minutes. **Pace:** narrate the *why* while each artifact renders.

---

## Before you record

- Set a descriptive `SEC_USER_AGENT` in `.env` (e.g. `"DealLens Diligence Lab (portfolio) you@example.com"`).
  SEC requires it; without it requests are throttled or blocked.
- Backend up. Either pre-seed real demo workspaces or create one live on camera:
  - `python -m src.seed.load_seed` — seeds real MSFT and CRWD workspaces from live SEC (needs network),
    then `uvicorn src.main:app --reload` (or `make seed` + `make dev`).
- Frontend up: `npm run dev` → `http://localhost:3000`.
- Confirm `GET /api/health` returns `"status":"ok"` (and `"database":"sqlite"` by default).
- If you want to show creation live, start with **no** CRWD workspace; otherwise pre-seed and skip to
  step 3.

---

## Opening line (say to camera)

> "DealLens Diligence Lab is a public-data AI diligence copilot. In the next few minutes I'll run a full
> first-pass diligence pack on a **real public company** — pulled live from SEC EDGAR — surfacing red
> flags from its 10-K, benchmarking against real public peers, drafting an investment-committee memo, and
> red-teaming the thesis, while keeping **every material claim source-grounded, traceable, and reviewable
> by a human.**"

---

## The journey

### Step 1 — Landing page & disclaimer
**Do:** Open `/`. **Say:** "This is a non-commercial portfolio project on public SEC data — outputs are
AI-assisted drafts for demonstration, not investment advice." **Point out:** the disclaimer banner and
the "New workspace" call to action.

### Step 2 — Create the workspace (enter a ticker)
**Do:** Click **New workspace** → `/workspaces/new`. Pick a `deal_type` (e.g. `software_platform`) and
enter a ticker: **CRWD**. Submit. **Say:** "I just entered a ticker. On submit, the backend resolves CRWD
to its SEC CIK, pulls XBRL company facts and recent filings, fetches the latest 10-K, extracts the risk
factors, and runs the full analysis — all before this page finishes loading." **Point out:** an unknown
ticker returns a clear 404; a network failure returns a 502 — it never invents data.

### Step 3 — Workspace overview
**Do:** Land on `/workspaces/[id]`. **Say:** "The command center — the diligence plan, top risks, and the
counts (filings, comps, risks, questions, evidence), all already populated from the live ingest." **Point
out:** the artifact checklist (plan, risks, questions, IC memo, bear case) is already complete because
creation ran the full pipeline.

### Step 4 — Target profile (real XBRL)
**Do:** Open `/workspaces/[id]/target`. **Say:** "CrowdStrike, straight from SEC XBRL — roughly $4.8B
revenue, ~22% growth, ~75% gross margin, and a **negative GAAP operating margin**, which pulls Rule-of-40
to about 16%." **Point out:** `data_source: SEC EDGAR (XBRL + 10-K)`, `is_synthetic: false`, and that
every figure links to its XBRL concept in the evidence table.

### Step 5 — Filings
**Do:** Open `/workspaces/[id]/filings`. **Say:** "The real 10-K / 10-Q / 8-K filings pulled from
`data.sec.gov`, each with its accession number and a link to the actual document on `sec.gov`. The latest
10-K was fetched and chunked by section — that's where the risk factors come from." **Point out:** the
`section_count` on the 10-K and that `is_synthetic` is false; mention the SEC `User-Agent` requirement.

### Step 6 — Add real peers
**Do:** Open `/workspaces/[id]/comps`. Add peer tickers: **PANW, ZS, S**. **Say:** "Peers are added by
ticker, and each one's financials come from the **same** XBRL pipeline as the target — real numbers, not
sample values. Adding peers re-runs the analysis so the benchmark and memo update." **Point out:** every
comp row is real XBRL; the valuation-multiple columns are intentionally blank.

### Step 7 — Financial benchmark
**Do:** View the benchmark on the same page. **Say:** "Deterministic target-vs-peer metrics on
SEC-reported fundamentals — scale above the median, gross margin in line, growth below the median, GAAP
operating margin below the median. **Market valuation multiples are omitted entirely** — there's no free
source, and we don't fabricate valuation." **Point out:** the Recharts visual and the omitted-multiples
note.

### Step 8 — Diligence plan
**Do:** Open the plan (generated on ingest; re-generate to show it live). **Say:** "A first-pass plan by
workstream — commercial, financial, product/tech, legal, cyber, management. GovCon is marked *not
applicable* unless federal exposure surfaces in the filing." **Point out:** objectives, key questions, and
evidence-needed per workstream.

### Step 9 — Risk / red-flag matrix
**Do:** Open `/workspaces/[id]/risks`. **Say:** "Findings from two deterministic sources: a keyword scan
of the real 10-K risk factors — legal/regulatory, AI-disruption, integration/M&A — and financial-metric
flags over XBRL: the negative GAAP operating margin and the GAAP net loss. Each is scored 1–10 and
**cited to evidence**." **Point out:** the `ClaimBadge`, the severity colors, and that text-based
severities are heuristic (keyword-based) and carry a follow-up asking to quantify the exposure.

### Step 10 — Diligence questions
**Do:** Open `/workspaces/[id]/questions`. **Say:** "The findings become prioritized questions by
workstream — e.g. an independent QoE and path-to-profitability stress test on the GAAP operating loss, and
quantifying the disclosed legal and AI-disruption risks." **Point out:** priority tags; finding-linked
questions carry the finding's evidence ref.

### Step 11 — IC memo
**Do:** Open `/workspaces/[id]/memo`. **Say:** "A draft investment-committee memo — executive summary,
financial profile from XBRL, the peer benchmark, findings, preliminary thesis, and next steps." **Point
out:** the *DRAFT FOR HUMAN REVIEW — NOT INVESTMENT ADVICE* banner and the inline `[EV-###]` citations.
Mention the optional live-LLM polish that improves flow but changes no numbers or citations.

### Step 12 — Red-team / bear case
**Do:** Open `/workspaces/[id]/red-team`. **Say:** "Now the system argues **against** its own thesis —
profitability is projected not proven, the disclosed risks may be underweighted — and it publishes the
claims it *couldn't* support and the evidence still missing." **Point out:** the unsupported-claims list
(with *why weak* and *recommended action*) and the missing-evidence list routed to workstreams.

### Step 13 — Evidence & audit table
**Do:** Open `/workspaces/[id]/evidence`. **Say:** "This is the point of the whole thing — every material
claim resolves to a row: the claim, its type (fact / calculation / inference / assumption), the SEC
source, the exact snippet or the calculation, and a confidence score. Financial facts trace to an XBRL
concept; qualitative flags quote the 10-K and link to the real `sec.gov` document." **Point out:** click a
`SourceCitation` in the memo and land on the matching `EV-###`.

### Step 14 — Recap & close
**Do:** Return to `/workspaces/[id]`. **Say:** "In a few minutes we went from a **ticker** to a full
first-pass pack — real financial profile, real peer benchmark, plan, red-flag matrix, questions, an IC
memo, a bear case, and an inspectable evidence trail behind every claim — grounded entirely in public SEC
data, with nothing fabricated and everything checkable."

---

## Closing line (say to camera)

> "The point is not to automate investment judgment. The point is to show how AI can accelerate the
> evidence-gathering, issue-spotting, memo-drafting, and red-team process **while keeping humans
> accountable for decisions.** Every number traces back to a real SEC filing, and none of it is
> investment advice."

---

## Optional B-roll / secondary flow

Show a second ticker to prove it's general — e.g. create an **MSFT** workspace with peers GOOGL / ORCL /
CRM, or **NVDA** — and note the whole pack rebuilds from that company's live XBRL and 10-K. Keep it short.
See [`docs/sec-ingestion.md`](./sec-ingestion.md).
