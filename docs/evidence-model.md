# Evidence model

The evidence model is the spine of DealLens. Every material claim in the diligence pack — a number in the
memo, a red flag in the matrix, a rationale behind a question — resolves to an **Evidence** row with a
stable human key (`EV-###`). This is what makes the pack auditable on **real** data: a reviewer can click
any claim and see the claim type, the SEC source (with its real `sec.gov` URL), the exact snippet, and a
confidence score. This document defines the Evidence object, the four claim types, the faithfulness rule,
and how refs flow through the system.

Every figure is either a real XBRL value or a real 10-K disclosure. Nothing is synthetic.

---

## The Evidence object

From [`docs/CONTRACTS.md`](./CONTRACTS.md):

```json
{
  "id", "workspace_id",
  "ref",            // stable human key, e.g. "EV-001"
  "claim",          // the assertion, in one sentence
  "claim_type",     // fact | calculation | inference | assumption
  "source_name",    // e.g. "CrowdStrike FY2025 10-K (XBRL: OperatingIncomeLoss)"
  "source_type",    // "xbrl" (financials) | "sec_filing" (10-K text) | "usaspending" (federal awards)
  "source_url" | null,       // the real sec.gov filing document URL
  "source_date" | null,      // filing date (or fiscal-year end)
  "source_section" | null,   // "XBRL company facts", or "Risk Factors (Item 1A)"
  "evidence_text",  // the verbatim 10-K snippet or the calculation expression
  "confidence",     // decimal in [0,1]
  "agent_name",     // which producer emitted it: financial_analyst, risk_analyst, ...
  "created_at"
}
```

- **`ref` is stable and human-readable** (`EV-001`, `EV-002`, …). Findings, questions, and memo passages
  cite this ref, not the UUID.
- **Refs are allocated sequentially per workspace.** `evidence_service.next_ref` numbers each new row as
  `EV-{count+1}`, so the first material claim in a workspace is `EV-001`. A full re-analysis clears and
  rebuilds the whole set deterministically (see below), so the numbering is stable for a given target and
  peer set.
- **`evidence_text` is the receipt** — for a `fact` from XBRL it names the concept and value; for a
  `fact` from the 10-K it is the quoted risk-factor sentence; for a `calculation` it is the expression
  (e.g. `Operating income / revenue = -6.0%`).
- **`source_type` tells you the origin.** Real financials (facts *and* the ratios/growth computed from
  them, including the multi-year **revenue CAGR**) carry `"xbrl"`. Qualitative findings quoted from the
  10-K carry `"sec_filing"` with the filing's real `source_url` and the section it came from. Federal
  contract facts from the GovCon workstream carry **`"usaspending"`** (agency concentration and recompete),
  with `source_url` `https://www.usaspending.gov/`.
- **`agent_name`** attributes the claim to its producer (`financial_analyst`, `risk_analyst`,
  `citation_auditor`, …).

---

## The four claim types

Not all claims are equal, and the model refuses to pretend they are. Every claim carries one of four
types, surfaced in the UI via a `ClaimBadge`.

| Claim type | Definition | Where it comes from in the real pipeline | How it is handled / surfaced | Typical confidence |
|---|---|---|---|---|
| **fact** | A statement drawn directly from a source | A reported XBRL line item (revenue, net income, cash, debt) **or** a quoted 10-K risk-factor sentence | Cited to its SEC source; `evidence_text` is the XBRL value or the quoted passage; highest trust | ~0.45–0.95 |
| **calculation** | A value computed deterministically from cited facts | A ratio/growth figure computed in `sec_financials` (margins, growth, R&D %, Rule-of-40) or a financial-flag arithmetic in `risk_analyst` | Marked `(calc)`; the reader can redo the math; **never** produced by a model | ~0.80–0.95 |
| **inference** | An analyst judgment derived from facts, not stated by the source | Reserved in the model for reasoned reads over facts/calcs | Marked `(inference)`; flagged as judgment that **requires validation** | ~0.55–0.65 |
| **assumption** | An unverified placeholder needed to proceed | Reserved in the model for explicitly-flagged assumptions | Marked `(assumption)`; called out as a **gating item** to confirm | ~0.40 |

Design principle in one line: **facts are sourced, calculations are computed, inferences are reasoned,
assumptions are flagged** — and the reader can always tell which is which.

> **What the deterministic engine emits today.** On real data, `run_full_analysis` produces `fact` and
> `calculation` rows: XBRL facts and calculations (`source_type "xbrl"`, including the multi-year revenue
> CAGR), 10-K risk-factor snippets (`fact`, `source_type "sec_filing"`), and — when a GovCon profile has
> been fetched — federal-award facts (`fact`, `source_type "usaspending"`). The `inference` and
> `assumption` types remain first-class in the
> schema, the `ClaimBadge`, and the confidence model — they are how a human annotator (or the optional
> live-LLM path) labels reasoned or unverified claims layered on top of the sourced base. The engine does
> not manufacture inferences or assumptions from thin air.

Why the distinction matters: the same headline can be a fact, an inference, or an assumption depending on
its evidentiary basis, and conflating them is exactly how a first-pass read misleads. A negative GAAP
operating margin is a **fact** from XBRL; that the company *will* reach profitability on plan is an
**unsupported claim** the red-team flags, not a fact. The model keeps that distinction visible instead of
laundering a projection into a load-bearing number.

---

## How evidence is built from real SEC data

`analysis_service.run_full_analysis` rebuilds the evidence set from scratch each run:

1. **Clear.** `evidence_service.clear` deletes the workspace's existing Evidence rows (and the risk /
   question / plan / memo / red-team artifacts that cite them).
2. **Financial evidence (XBRL).** For each populated metric — revenue, revenue growth, gross margin,
   operating margin, net income, net margin, R&D %, Rule-of-40, cash, total debt — a row is created with
   `source_type "xbrl"`, a `source_name` naming the XBRL concept (e.g. `... 10-K (XBRL: NetIncomeLoss)`),
   the filing `source_url`/date, `claim_type` `fact` for reported line items and `calculation` for
   ratios/growth, and confidence ~0.95 (fact) / ~0.9 (calc). When multi-year trend data is present, the
   **revenue CAGR** is written as an additional **`calculation`** row (`source_type "xbrl"`) — e.g.
   *"revenue CAGR was 12.3% over FY2021–FY2025"* — so the trajectory read is cited like any other number.
3. **Risk-finding evidence.** Each risk finding writes one Evidence row:
   - **Text findings** quote the most on-topic sentence from the matched 10-K risk-factor chunk —
     `claim_type` `fact`, `source_type "sec_filing"`, `source_section` = the 10-K section, `source_url` =
     the filing. Confidence scales with keyword density (~0.45–0.70).
   - **Financial flags** (e.g. negative GAAP operating margin, sub-threshold growth, net loss,
     debt ≫ cash) write a `calculation` row with the arithmetic in `evidence_text` and `source_type
     "xbrl"`.
   - **GovCon flags** *(only when a GovCon profile has been fetched)* write a `fact` row with
     `source_type "usaspending"` for each `govcon_risk` finding — agency concentration (top agency's share
     of federal contract obligations) and recompete exposure (top awards with a PoP end within 24 months) —
     with `source_url` `https://www.usaspending.gov/` and confidence ~0.78–0.80. See
     [`docs/govcon-and-macro.md`](./govcon-and-macro.md).

Because evidence is created **as** the artifacts that cite it are generated, the two are never out of
sync. Refs number sequentially in creation order (financial metrics first, then findings), which is why a
given target+peers reproduces the same `EV-###` map on every rebuild.

---

## The faithfulness rule — "no uncited material claims"

The governing rule: **every material claim must carry an evidence ref, and no citation may be fabricated.**
Two mechanisms enforce it.

1. **The citation auditor.** `agents/citation_auditor.py` extracts every `EV-###` from a memo
   (`extract_refs`) and checks that each resolves to a real Evidence row (`find_uncited` returns the set
   that does not). A ref that does not exist is a faithfulness violation.
2. **A pytest faithfulness check on real data.** `tests/test_no_uncited_material_claims.py` builds a
   **real** workspace (MSFT via live SEC EDGAR) and asserts the invariant end to end: every risk
   `evidence_ref` resolves; every high/critical risk is cited; every question ref (when present)
   resolves; and every `EV-###` cited in the IC memo and bear-case markdown resolves to a known evidence
   row. The live tests are **skipped** when EDGAR is unreachable, so CI still passes offline while the
   guarantee is exercised whenever the network is available.

The red-team step turns the rule outward: it publishes an explicit list of **unsupported claims** (with
*why weak* and a *recommended action*) and **missing evidence** (with *why it matters* and the owning
*workstream*). On real data these lean on the honest limits of the source — e.g. "the company will reach
profitability on plan" is flagged because the current GAAP operating margin is negative (a projection,
not a fact), and each keyword-derived risk severity is flagged as heuristic until the exposure is
quantified with primary data.

---

## How refs flow: evidence → risks / questions / memo

```
                ┌─────────────────────────────────────────────┐
   SEC data ──▶ │  agents emit CLAIMS + paired EVIDENCE rows   │
  (XBRL facts / │  facts (xbrl / sec_filing) + calculations     │
   10-K risk    │  each assigned a stable ref  EV-001 … EV-###  │
   factors)     └───────────────┬─────────────────────────────┘
                                │
                    ┌───────────┼───────────────┬───────────────┐
                    ▼           ▼               ▼               ▼
              RiskFinding   DiligenceQuestion   Memo (ic /     RedTeam
              .evidence_ref .evidence_ref       bear) markdown  .unsupported_claims
                 = EV-0xx      = EV-0xx          `[EV-###]` tags  / .missing_evidence
                    │           │               │               │
                    └───────────┴───────┬───────┴───────────────┘
                                        ▼
                          Evidence & Audit page (/evidence)
                    every EV-### resolves to claim, type, SEC source,
                            snippet/expression, confidence
```

- **RiskFinding** carries a single `evidence_ref` (the XBRL flag's calc row, or the 10-K risk-factor
  snippet).
- **DiligenceQuestion** carries the source finding's `evidence_ref` when it is finding-driven, or `null`
  for standard workstream-coverage questions.
- **Memo** markdown embeds bracketed `[EV-###]` tags that `MemoViewer` / `SourceCitation` resolve to
  evidence rows.
- **RedTeam** references refs inside its bear-case markdown and its `unsupported_claims` /
  `missing_evidence` rationales.

In the frontend this shows up as a `ClaimBadge` next to a claim and a `SourceCitation evidenceRef="EV-0xx"`
that links to the row on the Evidence & Audit table — where, for a filing-sourced fact, the reader can
open the real `sec.gov` document. The audit page is therefore a complete, deduplicated index of
everything the pack asserts.

---

## Confidence

`confidence` is a decimal in `[0,1]` recorded per Evidence row. It is descriptive, not a probability of
being "right": it encodes how well the current basis supports the claim. Reported XBRL facts and
deterministic calculations sit highest (sourced / computed, ~0.9–0.95); keyword-derived risk-factor facts
sit lower (~0.45–0.70) because the disclosure states a risk without quantifying it; inferences and
assumptions, when present, sit lower still by construction. The system cites the limits of its own data
rather than presenting a heuristic severity as a measured value.
