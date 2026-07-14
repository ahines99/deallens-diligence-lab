# Institutional private-deal demo

This 8–10 minute walkthrough shows DealLens as an internal private-equity diligence workbench. The
primary story is a private target moving from management data to an independently reviewed IC packet.
Public SEC data is a useful secondary workflow, not the source of private-company facts.

## Before recording

1. Start from a migrated database: `cd apps/api && python -m alembic -c alembic.ini upgrade head`.
2. Start the API and web app with `make dev`, or run `python -m uvicorn src.main:app --reload` and
   `npm.cmd run dev` in separate terminals.
3. Register the first owner at `/register`; keep `AUTH_REQUIRED=true` and
   `AUTH_ALLOW_REGISTRATION=false` after bootstrap.
4. Prepare a small management P&L CSV, one debt agreement, and one customer-contract text file. Use
   fictional data that is clearly labeled as user-submitted.
5. Create two users in the same organization: an associate who prepares work and an investment partner
   who performs independent approvals.

## Walkthrough

### 1. Portfolio command center

Open `/portfolio`. Explain the stage funnel, sector and strategy exposure, upcoming IC calendar,
readiness components, overdue work, critical risks, source health, and downside/covenant watchlists.
Filter by stage and fund, then export the current view to CSV. Emphasize that every card is aggregated
from governed deal, task, source, model, and IC records—not a separate dashboard dataset.

### 2. Create and govern a private target

Create a workspace without a ticker, select `private_company`, and link it to a fund and deal. Show the
workspace classification and external-LLM setting. Say that manually entered target values are stamped
as user-submitted and unverified; the client cannot relabel them as SEC/XBRL evidence.

### 3. Import management financials

Open Financial Data, upload the management P&L, and preview the dry run. Review mappings, period and
currency metadata, scaling, reconciliation output, and explicit exceptions before committing. Show the
sealed source snapshot and hash. Re-import a revised file to demonstrate an append-only version rather
than an in-place rewrite.

### 4. Build and approve the QoE bridge

Open QoE, create management and sponsor adjustments, and show reported, management-adjusted,
sponsor-adjusted, and run-rate EBITDA. Point to the evidence locator and materiality. Submit as the
associate, switch to the partner, and approve it. Demonstrate that the proposer cannot decide the same
adjustment and that missing values remain unknown instead of becoming zero.

### 5. Review private documents and claims

Open the Data Room, ingest the debt agreement and customer contract, then ask a cited question. Inspect
the exact document version, chunk locator, quoted span, and abstention behavior for unsupported answers.
Extract structured claims, reject or edit one, and approve another as the independent reviewer. The
approved revision is promoted to governed Evidence with exact document and chunk hashes.

### 6. Underwrite base, upside, and downside cases

Open the Underwriting workbench. Build sources and uses, monthly Y1–Y2 and annual Y3–Y5 projections,
debt tranches, cash sweep, covenants, DCF, and sponsor returns. Bind the approved private claim to the
case before saving. Save all three scenarios and show immutable versions, parent version, canonical
input/output hashes, and stale-output invalidation when an assumption changes.

Run entry/exit sensitivity, reverse stress, and the working-capital peg. Call out liquidity shortfalls,
covenant breaches, and debt-service defaults. Avoid presenting an old result after editing inputs.

### 7. Govern the model and assemble the IC packet

Submit the chosen cases as the associate and approve them as the partner. Add thesis and risk ledger
items with evidence references. Open the IC composer, select the cases and any additional approved
claims, then create the packet. Explain that the server assembles snapshots from persisted records;
client-authored model, evidence, or thesis snapshots are rejected.

Show readiness checks and source-currentness. The claims already bound to model cases are automatically
carried into the packet. Submit the packet, switch actors, and record the independent IC decision.

### 8. Verify the export

Create the JSON export manifest and run verification. Show canonical packet binding, section hashes,
evidence/document/chunk bindings, case input/output hashes, and decision metadata. Explain that packet
regeneration creates a new immutable version atomically; failure does not destroy the last good packet.

### 9. Close on operational honesty

Return to the portfolio. Show the activity timeline and updated readiness. Briefly open the signal panels
and explain their explicit `available`, `partial`, and `unavailable` states: an upstream SEC, FRED,
USAspending, Form 4, EFTS, or GDELT failure is never displayed as a clean zero or “no events.”

## Suggested closing

> DealLens does not replace investment judgment. It makes the path from source material to an IC
> decision inspectable: who supplied a value, who approved it, which exact source supports it, which
> model version used it, and whether the exported packet still verifies.

## Optional public-data appendix

For a second, shorter flow, create a public workspace with AAPL, MSFT, or CRWD. Demonstrate SEC company
facts, exact fiscal-year labels for non-calendar issuers, filing accession links, FRED observations,
Form 4 ownership-code parsing, USAspending pagination, and explicit source outage states. Keep this
appendix separate from the private-deal narrative so public data is never implied to verify management
financials.
