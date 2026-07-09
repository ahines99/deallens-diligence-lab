# Disclaimers

DealLens Diligence Lab is an independent, non-commercial portfolio project. It builds diligence packs on
**real public companies** from **public SEC EDGAR data**. This page collects the full disclaimer text and
the labeling conventions the app uses for AI-assisted outputs, heuristic severities, and the
not-investment-advice posture. The `Callout` / `DisclaimerBanner` components surface these in the UI
wherever they are relevant.

---

## Primary disclaimer

> DealLens Diligence Lab is an independent, non-commercial portfolio project using public data (primarily
> SEC EDGAR). It is not affiliated with, endorsed by, or sponsored by any investment firm, private equity
> firm, public company, data vendor, or AI platform vendor. Outputs are **AI-assisted, deterministic
> drafts** for educational and demonstration purposes only, are **not investment advice**, and should not
> be used to make investment decisions. Qualitative risk severities are heuristic and require human
> validation; market/valuation data is omitted.

---

## Not investment advice

Nothing produced by DealLens — the target overview, comps, benchmark, risk matrix, diligence questions,
IC memo, or bear-case memo — is investment advice, a recommendation, an offer, or a solicitation to buy or
sell any security or to pursue any transaction.

- Both memos render a **"DRAFT FOR HUMAN REVIEW — NOT INVESTMENT ADVICE"** banner.
- Outputs are **first-pass drafts** intended to accelerate an analyst's work, not to replace judgment. A
  human is accountable for every decision.
- No output should be relied upon for any actual investment, financing, or diligence decision.

---

## What the outputs are (and are not)

The pack is built on **real public data**, but it is a first-pass, automated draft — treat it accordingly:

- **Real, sourced financials.** Target and peer financials come from **SEC EDGAR company facts (XBRL)**.
  Each figure is attributed to its XBRL concept and the filing it came from, and is a **GAAP** value as
  standardized in company facts (which can differ materially from a company's non-GAAP reporting).
- **Real filing text, heuristically parsed.** Qualitative risk findings quote the latest **10-K's Item 1A
  risk factors**. The section extraction is a **text heuristic** (largest-span item detection), and the
  keyword scan that produces findings is deterministic but approximate — it can miss or mis-bound content
  in unusual filing layouts.
- **Heuristic, keyword-based severities.** A risk's severity reflects **how prominently the topic is
  discussed** in the filing (keyword density), **not a measured magnitude of exposure**. Severities are a
  triage signal that **requires human validation**, not a verdict. The red-team step states this
  explicitly and asks for each high-severity item to be quantified with primary data.
- **Deterministic, then optionally re-voiced.** Every number and citation is produced in code. When
  `LLM_MODE=live`, an LLM may re-voice the memo prose for clarity, but it is instructed to change no
  number, fact, or citation and to keep every `[EV-###]` tag in place; any failure falls back to the
  deterministic text.

---

## Market / valuation data is omitted

DealLens does **not** populate market capitalization, enterprise value, or EV/Revenue (or any other
valuation multiple):

- There is **no free, reliable source** for live market data, and the project's rule is to **omit rather
  than fabricate**. The `market_cap`, `enterprise_value`, and `ev_revenue_multiple` fields exist in the
  schema but are always `null`.
- Benchmarking is therefore done on **SEC-reported fundamentals only** (revenue, growth, gross/operating/
  net margin, R&D %, Rule-of-40). The benchmark summary and the IC memo state that valuation multiples are
  omitted and support no valuation conclusion.

---

## Peer comparisons are directional

Comparable companies are **real public companies** added by ticker, with financials pulled from the same
XBRL pipeline as the target:

- Peer fiscal-year periods may differ, so cross-company comparisons are **directional**, not precise.
- A peer metric is shown only where its XBRL concept is available; otherwise it is omitted (`null`),
  never estimated.

---

## Public-data sourcing and terms of use

The primary flow draws only on free, public SEC EDGAR data; the extension sources (SEC Financial Statement
Data Sets, FRED, OpenFIGI, GDELT, SAM.gov, USAspending) are also free. See
[`docs/data-sources.md`](./data-sources.md).

- Public data remains subject to the **terms of use of its original providers**.
- SEC endpoints require a descriptive **`User-Agent`** header under SEC's fair-access policy; DealLens
  honors this (`SEC_USER_AGENT`) and the documented rate limits. See
  [`docs/sec-ingestion.md`](./sec-ingestion.md).
- DealLens is **not affiliated with, endorsed by, or sponsored by** the SEC, the Federal Reserve,
  Bloomberg/OpenFIGI, GDELT, any U.S. government agency, any public company referenced, or any data or
  AI-platform vendor.

---

## Faithfulness commitments

These are the operating principles the codebase enforces (and, for the citation rule, tests on real data):

1. Every material claim is tied to real evidence — an XBRL concept or a 10-K passage — where possible.
2. Facts, calculations, inferences, and assumptions are labeled distinctly.
3. Outputs are never presented as investment advice.
4. **Citations are never fabricated** — every `EV-###` cited must resolve to a real evidence row.
5. Missing financials are **omitted, not invented**; **market/valuation multiples are omitted entirely**
   (no free source).
6. Qualitative severities are **heuristic** and flagged as requiring human validation.
7. LLMs may draft/re-voice narrative, but calculations are deterministic and auditable.

See [`docs/evidence-model.md`](./evidence-model.md) for how the "no uncited material claims" rule is
enforced by the citation auditor and the `test_no_uncited_material_claims` pytest check (which runs
against a real SEC workspace and is skipped when EDGAR is unreachable).

---

## License

The project is released under the **MIT License** (see `LICENSE`). Public data remains subject to the
terms of its original providers. Use of the code does not grant any rights in third-party data.
