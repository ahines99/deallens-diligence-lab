# Example case study: CrowdStrike (CRWD)

> **ILLUSTRATIVE OF THE PIPELINE — NOT INVESTMENT ADVICE.** This walkthrough shows the shape of the pack
> DealLens builds for a **real** public company, using CrowdStrike Holdings (NASDAQ: **CRWD**) as the
> example. CrowdStrike is a real company and the figures below are **rounded, order-of-magnitude values
> chosen to illustrate how the pipeline behaves** — they are not a current, audited financial statement
> and may not match the latest filing. When you run DealLens on CRWD it pulls the **live** numbers from
> SEC EDGAR (XBRL company facts) and the real Item 1A risk factors from the latest 10-K, so your pack
> will differ in the exact values while following exactly this structure. `[EV-###]` tags are the
> sequential evidence refs the engine allocates per workspace. Nothing here is a recommendation.

This narrative mirrors what the ticker-driven flow produces end to end: the profile from XBRL, the
investment question, the benchmark against real peers, the red-flag matrix (10-K risk factors + financial
flags), and the bear case.

---

## 1. The profile (from SEC XBRL)

Create a workspace with ticker **CRWD**; the backend resolves it to a CIK, pulls company facts, and
derives the financial profile — each figure cited to its XBRL concept.

**Financial snapshot** (illustrative of the pipeline; live values come from XBRL):

| Metric | Value | Claim type | Evidence |
|---|---|---|---|
| Revenue | ~$4.8B | fact | `[EV-001]` |
| Revenue growth (YoY) | ~22% | calculation | `[EV-002]` |
| Gross margin | ~75% | calculation | `[EV-003]` |
| **GAAP operating margin** | **~ −6%** | calculation | `[EV-004]` |
| Net income | GAAP net loss (small) | fact | `[EV-005]` |
| Net margin | slightly negative | calculation | `[EV-006]` |
| R&D % of revenue | ~20% | calculation | `[EV-007]` |
| **Rule of 40** | **~16%** (22% growth + −6% operating margin) | calculation | `[EV-008]` |
| Cash | ~$4.0B | fact | `[EV-009]` |
| Total debt | ~$0.7B (well below cash) | fact | `[EV-010]` |

The headline tension is visible immediately: strong ~75% gross margin and ~22% growth, but a **negative
GAAP operating margin** that pulls Rule-of-40 down to ~16% — far below the 40 benchmark. The description
and sector are populated from the 10-K Business section and the submission's SIC description.

> **Note on the profile.** These are GAAP figures straight from XBRL. CrowdStrike, like many software
> companies, reports much stronger *non-GAAP* profitability; DealLens deliberately benchmarks **GAAP**
> because that is what is standardized and citable in company facts. Market **valuation multiples are
> omitted** — there is no free source, and the project does not fabricate them.

---

## 2. The investment question

The workspace defaults the question from the resolved company; a reviewer can override it. For this deal
type (`software_platform`):

> *"Is CrowdStrike Holdings (CRWD) an attractive investment at its current financial profile and risk
> posture?"*

The plan frames the priorities as validating the path to GAAP profitability, testing growth durability
against the disclosed risks, and pressure-testing the qualitative red flags surfaced from the 10-K.
GovCon diligence is marked *not applicable* unless federal-contract language surfaces in the risk
factors.

---

## 3. Key findings

**The attractive side.** A large-scale (~$4.8B revenue), high-gross-margin (~75%) security platform still
growing ~22% with a strong cash position (~$4.0B) and modest debt (~$0.7B) is a credible franchise. The
recurring, mission-critical nature of endpoint/cloud security supports a durable-revenue thesis.

**The tension the pack elevates before pricing:**

1. **GAAP profitability.** The ~ −6% GAAP operating margin `[EV-004]` makes the path to profitability an
   assumption, not a fact — and it drags Rule-of-40 to ~16% `[EV-008]`.
2. **Legal / regulatory exposure** disclosed in the 10-K risk factors `[EV-011]`.
3. **AI / technology disruption** — the risk factors flag AI both as an opportunity and as a competitive
   threat `[EV-012]`.
4. **Integration / M&A** — an active acquisition cadence carries integration risk `[EV-013]`.

---

## 4. Top red flags

The matrix merges two deterministic sources — a taxonomy keyword scan of the real Item 1A risk factors,
and financial-metric flags over XBRL — then sorts by severity. Illustrative result:

| Severity (score) | Finding | Category | Source | Evidence |
|---|---|---|---|---|
| **High (7)** | Litigation / regulatory exposure discussed in the 10-K risk factors (e.g. securities and consumer litigation, regulatory inquiries, evolving data/privacy regimes) | Legal & regulatory | 10-K Item 1A | `[EV-011]` (fact, `sec_filing`) |
| **High (7)** | AI/automation cited as a competitive-disruption and product-execution risk to the moat | AI / tech disruption | 10-K Item 1A | `[EV-012]` (fact, `sec_filing`) |
| **Medium (6)** | Acquisition-integration risk — an active M&A cadence raises integration, retention, and tech-debt exposure | Integration & M&A | 10-K Item 1A | `[EV-013]` (fact, `sec_filing`) |
| **Medium (6)** | Negative GAAP operating margin (~ −6%) — not yet profitable on a GAAP operating basis | Margin pressure | XBRL flag | `[EV-014]` (calculation, `xbrl`) |
| **Medium (5)** | GAAP net loss — assess cash runway and reliance on stock-based comp / financing | Debt & liquidity | XBRL flag | `[EV-015]` (calculation, `xbrl`) |

Notes on how these arise:

- The **text findings** (`[EV-011]`–`[EV-013]`) quote the most on-topic sentence from the matched 10-K
  risk-factor chunk. Their severities are **heuristic** (keyword density), so each carries a follow-up
  asking to *quantify the exposure beyond the risk-factor language*.
- The **financial flags** (`[EV-014]`–`[EV-015]`) fire from fixed rules: negative GAAP operating margin
  (medium at ~ −6%, since it is not below −10%) and a GAAP net loss (medium). The **growth flag** — which
  fires only when revenue growth is below 8% — **does not** trigger here at ~22%; the honest read is that
  growth is healthy but does not offset the operating loss, which is why Rule-of-40 lands at ~16%.
- On a real run, CrowdStrike's security-vendor disclosures may also trip **cyber & data security**; the
  exact set depends on the live 10-K.

---

## 5. The benchmark read (real peers)

Add real peers **by ticker** — PANW, ZS, S — and each peer's financials are fetched from the **same** XBRL
pipeline as the target (not sample values). Illustrative benchmark on SEC-reported fundamentals:

| Ticker | Company | Revenue | Gross margin | Rev growth | GAAP op. margin |
|---|---|---|---|---|---|
| PANW | Palo Alto Networks | ~$8.0B | ~74% | ~15% | ~ +8% |
| ZS | Zscaler | ~$2.2B | ~78% | ~30% | ~ −2% |
| S | SentinelOne | ~$0.8B | ~74% | ~35% | ~ −30% |
| — | **Peer median** | ~$2.2B | **~74%** | **~30%** | **~ −2%** |
| — | **CRWD (target)** | **~$4.8B** | **~75%** | **~22%** | **~ −6%** |

The read the benchmark produces:

- **Scale above the peer median** — ~$4.8B vs. ~$2.2B (assessment: *above*).
- **Gross margin in line** — ~75% vs. ~74% median (*in line*).
- **Growth below the median** — ~22% vs. ~30% (*below*), reflecting a larger revenue base.
- **GAAP operating margin below the median** — ~ −6% vs. ~ −2% (*below*); the peer set spans from PANW's
  GAAP profitability to SentinelOne's deep operating loss, so the read is directional.
- **Rule of 40 below the peer median** — ~16% vs. a peer median around the low-20s.
- **No valuation conclusion.** `market_cap`, `enterprise_value`, and `ev_revenue_multiple` are **omitted
  (null)** — there is no free market-data source, and the project does not fabricate valuation. The
  benchmark summary and the memo say so explicitly.

---

## 6. The bear case

The red-team memo argues the skeptical side, using CrowdStrike's own SEC disclosures. Because the GAAP
operating margin is negative, the profitability thread fires automatically:

1. **Profitability is an assumption, not a fact.** The ~ −6% GAAP operating margin `[EV-004]` means the
   path to profitability is projected; it is sensitive to growth and spend discipline, and Rule-of-40 at
   ~16% `[EV-008]` leaves little cushion.
2. **The disclosed risks may be underweighted by the base case** — litigation/regulatory `[EV-011]`,
   AI-driven competitive disruption `[EV-012]`, and integration risk from the M&A cadence `[EV-013]`.
3. **What would break the thesis** — a disclosed risk factor proving quantitatively material once
   diligenced; growth or margin deterioration beyond the current trajectory; or quality-of-earnings
   adjustments that reduce the sustainable profit base.

**Unsupported claims the red-team flags** (do not rely on until confirmed):

- *"CrowdStrike will reach GAAP profitability on plan"* — current GAAP operating margin is negative;
  profitability is projected, not demonstrated. **Action:** stress-test the path-to-profitability model
  under a downside case.
- *"The legal/regulatory (or AI-disruption, or integration) risk is manageable at the current severity"*
  — each is drawn from 10-K risk-factor **language**, which states the risk without quantifying exposure;
  the severity is heuristic. **Action:** quantify each exposure with primary data.

**Missing evidence** (routed to workstreams): an independent quality-of-earnings on the GAAP-to-cash
bridge (financial); cohort retention / unit economics that filings rarely disclose (customer); and a
quantified magnitude for each high-severity risk factor (commercial/legal/technology).

---

## 7. Bottom line

The illustrative read is that CRWD is a large, high-margin, cash-rich security franchise whose base case
**should not be priced** until the path to GAAP profitability, the disclosed legal/regulatory and
AI-disruption risks, and the integration exposure are quantified. The recommended next steps route each
item to a workstream: independent QoE and path-to-profitability stress test (financial); litigation and
regulatory-regime review with counsel (legal); AI-moat and integration assessment (technology/commercial).

This is a **draft for human review**, produced deterministically over **real** SEC EDGAR data, then
optionally re-voiced by an LLM that changes no numbers. It is **not investment advice**. Run the ticker
yourself to see the live figures. See [`docs/disclaimers.md`](./disclaimers.md).
