# Diligence methodology

DealLens models a **first-pass diligence** workflow on a **real public company**: the
evidence-gathering, issue-spotting, memo-drafting, and red-team steps an investment team runs before
committing analyst time to a deep dive. The goal is not to automate judgment — it is to compress the
mechanical work while keeping a human accountable for every decision. This document describes the
ticker-driven workflow, the risk taxonomy, severity scoring, the question workstreams, and the
human-in-the-loop principle.

---

## The workflow

Everything starts from a **ticker**. Creating a workspace with one resolves the company against SEC
EDGAR, ingests its XBRL financials and latest 10-K, and runs the full analysis in a single pass.

```
Ticker ─▶ SEC EDGAR ingest ─▶ Target (XBRL financials) + 10-K risk factors (chunks)
   │                                      │
   ├─ Peer tickers ──▶ Financial benchmark ◀── (real XBRL for each peer)
   ├─ Risk / red-flag matrix ◀───────────────────────────────┤
   ├─ Diligence questions (by workstream) ◀───────────────────┤
   ├─ IC memo draft ◀─────────────────────────────────────────┤
   ├─ Bear-case / red-team memo ◀─────────────────────────────┤
   └─ Evidence & audit table (every material claim) ◀──────────┘
```

| # | Stage | What happens | Primary output |
|---|---|---|---|
| 1 | **Workspace + ticker** | Frame the deal (`deal_type`, investment question) and enter a ticker; name/question default from the resolved company | `Workspace` |
| 2 | **SEC ingest** | Resolve ticker → CIK; pull submissions + company facts (XBRL); fetch the latest 10-K; extract Item 1A into section chunks | `Target` + `Filing[]` + chunks |
| 3 | **Financial profile** | Derive revenue, growth, margins, R&D %, Rule-of-40, cash, debt from XBRL — each with its source point | `Target` financials + `Evidence` (facts/calcs) |
| 4 | **Public comps** | Add real peers by ticker; fetch each peer's XBRL from the same pipeline | `ComparableCompany[]` |
| 5 | **Financial benchmark** | Deterministically compare target vs. peer fundamentals (median/min/max, assessment) | `FinancialBenchmark` |
| 6 | **Risk / red-flag matrix** | Keyword-scan the 10-K risk factors against the taxonomy + apply deterministic financial flags; score and cite each finding | `RiskFinding[]` |
| 7 | **Diligence questions** | Generate prioritized questions by workstream, finding-driven ones tied to evidence | `DiligenceQuestion[]` |
| 8 | **IC memo** | Draft the investment committee memo over the cited evidence | `Memo` (`ic_memo`) |
| 9 | **Red-team / bear case** | Argue the skeptical side; flag unsupported claims and missing evidence | `RedTeam` (+ `bear_case` memo) |
| 10 | **Evidence & audit** | Every material claim resolves to an inspectable `EV-###` row with its SEC source | `Evidence[]` |

Creating the workspace runs stages 2, 3, 6–10 automatically. Each `generate` endpoint is idempotent and
re-runnable, and adding peers re-runs the analysis so the benchmark and memo stay in sync. Every step
that emits a material claim also writes the Evidence rows it cites, so the audit table is always complete
(see [`docs/evidence-model.md`](./evidence-model.md)).

### Real-data overlays: trends, macro, GovCon

Three keyless, real-data overlays extend the read beyond a single fiscal year of one filing. Each is
optional and additive; full derivations are in [`docs/govcon-and-macro.md`](./govcon-and-macro.md).

| Overlay | Endpoint | What it adds to diligence |
|---|---|---|
| **Multi-year trend read** | `GET /trends` | Revenue + gross/operating/net margin and R&D % for the last five fiscal years, plus a **revenue CAGR** — trajectory, not just a snapshot. Computed at ingestion from the same XBRL (`extract_trends`); the CAGR is a cited `calculation` and appears as a memo row. |
| **Macro sensitivity** | `GET /macro` | A sector-aware **FRED** overlay (rates, inflation, unemployment, industrial production, GDP) mapped from the target's SEC sector, with latest value + YoY. Context for reasoning about rate/demand sensitivity — **context, not a forecast**; it does not by itself generate findings. |
| **GovCon workstream** | `GET`/`POST /govcon` | For federal contractors, a real **USAspending** contract profile — total obligations, **agency concentration** (top agency's share), and **recompete exposure** (top awards with a PoP end within ~24 months). `POST` re-runs the analysis so GovCon risk findings and the memo's GovCon section fold in. |

The multi-year trend and revenue CAGR feed the financial read (stage 3 / the memo); the GovCon profile
feeds the **risk matrix** through `govcon_flags` (below) and the memo's GovCon section.

---

## Risk taxonomy (10 categories)

The red-flag scanner works against a fixed taxonomy (`seed/risk_taxonomy.json`). Each category has a
human label, a default owning workstream, and a set of **signal phrases** the scanner matches against the
real 10-K risk-factor / MD&A chunks.

| Slug | Label | Owning workstream | What it captures |
|---|---|---|---|
| `customer_concentration` | Customer concentration | Commercial | Revenue dependence on a few customers |
| `supplier_concentration` | Supplier / vendor concentration | Product & technology | Dependence on key suppliers or a single cloud/infra provider |
| `demand_weakness` | Demand weakness | Commercial | Slowing growth, softening bookings, lengthening sales cycles |
| `margin_pressure` | Margin pressure | Financial | Rising costs, pricing pressure, unfavorable mix, hosting cost inflation |
| `debt_liquidity` | Debt & liquidity | Financial | Covenant pressure, refinancing risk, runway, working-capital strain |
| `legal_regulatory` | Legal & regulatory | Legal & regulatory | Litigation, investigations, compliance exposure, regime dependence |
| `cyber_security` | Cyber & data security | Cybersecurity | Breach history, posture gaps, sensitive-data handling |
| `integration_ma` | Integration & M&A | Commercial | Integration risk, roll-up complexity, tech debt from prior deals |
| `ai_tech_disruption` | AI / technology disruption | Product & technology | Automation/AI threat to the moat, platform / vendor lock-in |
| `govcon_risk` | GovCon / contract risk | GovCon | Federal contract concentration, recompete risk, appropriation exposure |

---

## Where risk findings come from

Findings are produced by up to three deterministic sources, all cited to real evidence:

**(a) Taxonomy keyword scan of the real 10-K risk factors.** `agents/risk_analyst.py` (`scan_text`)
prefers the *Risk Factors (Item 1A)* and *MD&A (Item 7)* chunks and, for each taxonomy category, picks the
highest-scoring chunk (score = distinct matched signals × 2 + total term frequency), with a minimum
score threshold so weak matches are dropped. Severity scales with signal density
(`score = min(9, 3 + distinct_signals + hits // 2)`), and the most on-topic sentence in that chunk is
quoted verbatim as the evidence snippet (`claim_type: fact`, `source_type: "sec_filing"`).

**(b) Deterministic financial-metric flags over XBRL.** `financial_flags` applies fixed rules to the
target's reported financials, each emitting a `calculation`-type finding with the arithmetic as evidence:

| Rule (from real XBRL) | Category | Band / score |
|---|---|---|
| Negative GAAP operating margin | Margin pressure | high (7) if < −10%, else medium (6) |
| Revenue growth < 8% | Demand weakness | high (7) if contracting, else medium (5) |
| GAAP net loss | Debt & liquidity | medium (5) |
| Total debt > 2× cash | Debt & liquidity | medium (5) |
| Gross margin < 50% | Margin pressure | low (3) |

**(c) GovCon flags over real USAspending contract data** *(only when a GovCon profile has been fetched)*.
`govcon_flags` turns the federal-contract profile into `govcon_risk` findings (workstream owner `govcon`),
each cited to a **`source_type: "usaspending"`** Evidence `fact` row. This is what **populates the
`govcon_risk` category with real data** — for a federal contractor, not just the taxonomy keyword scan:

| Rule (from real USAspending awards) | Category | Band / score |
|---|---|---|
| Top agency ≥ 50% of federal contract obligations | GovCon / contract risk | high (7) if ≥ 65%, else medium (6) |
| ≥ 1 major award with a PoP end within 24 months (recompete) | GovCon / contract risk | high (7) if recompete value > 20% of total, else medium (5) |

Findings from all applicable sources are merged and sorted by `severity_score`. Each finding must cite an
Evidence `ref` and carries a routed `follow_up_question`. Because every source is deterministic, the same
target (and, for GovCon, the same USAspending snapshot) reproduces the same matrix on every rebuild.

---

## Severity scoring (1–10 scale)

Every `RiskFinding` carries a `severity_score` on a **1–10** scale and a categorical `severity` band
(`severity_scale` in `risk_taxonomy.json`):

| Band | Score range | Reading |
|---|---|---|
| **low** | 1–3 | Minor / manageable; note and monitor |
| **medium** | 4–6 | Material; requires confirmatory work in the owning workstream |
| **high** | 7–8 | Serious; can shape price, structure, or the go/no-go |
| **critical** | 9–10 | Potentially deal-breaking; must be cleared before proceeding |

A finding also records **`likelihood`** (`low`/`medium`/`high`) and a **`confidence`** decimal in `[0,1]`.
Severity is about *impact*; likelihood about *probability*; confidence about *how well the current
evidence supports the finding* — deliberately separate so a high-impact-but-uncertain item is not
silently downgraded just because it is only qualitatively disclosed.

> **Honesty note on severity.** Text-based severities are **heuristic** — they reflect how prominently a
> risk is discussed in the 10-K (keyword density), not a measured magnitude of exposure. They are a
> triage signal for human diligence, not a verdict. The red-team step re-states this explicitly and asks
> for each high-severity item to be quantified with primary data.

Each finding's `follow_up_question` is routed to its `workstream_owner`, feeding the diligence-question
list.

---

## Question workstreams

Diligence questions are organized by **workstream** so the pack drops cleanly into how a deal team divides
labor. Finding-driven questions carry that finding's `evidence_ref`; standard/coverage questions may have
a `null` ref. Templates live in `seed/diligence_question_templates.json` and are assembled with the real
target's name and findings by `agents/diligence_lead.py`.

| Workstream | Label | Focus |
|---|---|---|
| `commercial` | Commercial diligence | Demand durability, competitive position, win/loss, bookings drivers |
| `product_technology` | Product & technology diligence | Architecture, moat, cloud dependency, AI-disruption exposure, tech debt |
| `financial` | Financial diligence | Quality of earnings, unit economics, margin bridge, path to profitability |
| `customer` | Customer diligence | Retention by cohort, concentration, contract terms, implementation drag |
| `market` | Market diligence | TAM/SAM, cyclicality, adjacent regulatory regimes |
| `legal_regulatory` | Legal & regulatory diligence | Litigation, IP/open-source, data-handling, license transferability |
| `cybersecurity` | Cybersecurity diligence | Breach history, certifications (SOC 2 / ISO 27001), PII controls |
| `ai_data` | AI / data diligence | Data rights, model governance, AI-native competitive exposure |
| `management` | Management diligence | Bench strength, key-person risk, post-close retention/equity |
| `govcon` | GovCon diligence | Federal ARR share, recompete, agency concentration (if applicable) |

Workstreams that do not apply are explicitly closed rather than left ambiguous — for a commercial target
with no federal exposure surfaced in the 10-K, the **GovCon** workstream is marked *complete / not
applicable* with no open questions, so a reviewer can see it was considered and dismissed. For a **federal
contractor**, by contrast, the GovCon workstream is now backed by **real USAspending data**: fetching the
GovCon profile (`POST /govcon`) populates `govcon_risk` findings (agency concentration, recompete) from
actual federal contract awards, and the memo gains a Federal Contract Profile section — see
[`docs/govcon-and-macro.md`](./govcon-and-macro.md).

Questions carry a **`priority`** (`low`/`medium`/`high`). The red-team step re-surfaces the
highest-priority, most load-bearing questions as gating items before pricing.

---

## Human-in-the-loop principle

> The point is not to automate investment judgment. The point is to show how AI can accelerate the
> evidence-gathering, issue-spotting, memo-drafting, and red-team process while keeping humans
> accountable for decisions.

Concretely, the methodology encodes accountability at every step:

1. **Every material claim is tied to real evidence** — a `ClaimBadge` and a resolvable `EV-###` that, for
   filing-sourced facts, links to the actual `sec.gov` document.
2. **Facts, calculations, inferences, and assumptions are labeled distinctly** — the reader always knows
   whether a statement is sourced from XBRL/the 10-K, computed, judged, or merely assumed.
3. **Qualitative severities are heuristic and flagged as such** — keyword-based triage that must be
   validated by a human against the filing.
4. **Outputs are drafts, not decisions.** Both memos are marked *DRAFT FOR HUMAN REVIEW — NOT INVESTMENT
   ADVICE*.
5. **Nothing is fabricated.** Missing financials are omitted; **market/valuation multiples are omitted
   entirely** (no free source) rather than invented.
6. **Calculations are deterministic and auditable** — the optional LLM re-voices prose but numbers come
   from code.
7. **The red-team is a first-class step** — the system argues against its own thesis and publishes what
   it could not support and what evidence is still missing.

The result is a pack a human can trust *because they can check it* — every number back to XBRL, every
qualitative flag back to a 10-K passage — not because they are asked to take the model's word.
