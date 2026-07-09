# DealLens Extension Build Spec (Wave 2)

Shared spec for the execution agents. Response shapes MUST match `apps/web/src/lib/types.ts`
(the frontend contract) and the endpoint table in `docs/CONTRACTS.md`. All money in USD, ratios as
decimals in [0,1], generated_at = ISO datetime.

## Shared data access (DO NOT re-fetch what's already stored)
- `target = workspace_service.get_target(session, workspace_id)`.
- **Headline financials**: `target.revenue, revenue_growth, gross_margin, operating_margin, net_income,
  net_margin, rnd_pct, rule_of_40, cash, total_debt, fiscal_year_end`.
- **Per-year forensic inputs** (already extracted at ingestion): `target.financials["forensic_inputs"]`
  = `{"years": ["2020".."2025"], "by_year": {"2025": {field: value|None, ...}, ...}}`. Fields:
  `assets, current_assets, current_liabilities, total_liabilities, receivables, inventory, payables,
  retained_earnings, equity, ppe_net, ltd_current, short_debt, ltd, cash, revenue, cogs, gross_profit,
  operating_income, net_income, cfo, capex, da, tax, interest, sga, sbc, rnd, shares_out, shares_diluted`.
  Any field may be `None` (notably `da`, `short_debt`, `interest`) — **degrade gracefully; never impute**.
- `target.financials["trends"]` (revenue/margin history) and `["sources"]` (XBRL source points) also exist.
- FRED via `fred_service` (keyless). SEC via `edgar_client` (keyless, needs `SEC_USER_AGENT`). USAspending via `usaspending_service`.

## Integration rules (avoid collisions)
- Each backend agent: create its `*_service.py` + `schemas/<name>.py` + `routers/<name>.py`. Register the
  router by adding it to the router list in `src/main.py` — **but if two agents edit main.py concurrently
  they conflict**, so instead EXPORT your router and let the integration agent wire it. To be safe: DO create
  the router file, DO NOT edit `src/main.py`, `src/services/analysis_service.py`, `src/agents/risk_analyst.py`,
  or `src/agents/ic_memo_writer.py`. The integration agent wires all of those.
- Forensics/events/insiders that should raise red flags: expose a module-level
  `def risk_flags(session, workspace_id) -> list[dict]` returning finding dicts in this shape (same as
  `RiskAnalyst.financial_flags`), which the integration agent will splice into `analysis_service`:
  ```
  {"risk_category","risk_category_label","title","finding","severity","severity_score","likelihood",
   "confidence","workstream_owner","follow_up_question",
   "evidence": {"claim","claim_type","evidence_text","source_name","source_type","source_url","source_date",
                "source_section","confidence","agent_name"}}
  ```
- Compute-on-GET (no new DB model needed): forensics, valuation, lbo, events, insiders, themes, news, filing-watch.
  Only auto-comps and refresh mutate state.
- Add a `NotFound` (from `src.services.common`) when a target/financials is missing so routers 404 cleanly.
- Tests go in `apps/api/tests/` and MUST be network-guarded (reuse `conftest.py`'s `live_workspace_id`/`sec_online`
  fixtures for anything hitting SEC/GDELT; pure-math goes in offline unit tests).

## Endpoints (match types.ts exactly)
- `GET  /api/workspaces/{id}/forensics` -> Forensics
- `GET  /api/workspaces/{id}/valuation` -> Valuation
- `POST /api/workspaces/{id}/lbo` (body LboInputs) -> LboResult
- `GET  /api/workspaces/{id}/events` -> EventTimeline
- `GET  /api/workspaces/{id}/insiders` -> InsiderActivity
- `GET  /api/workspaces/{id}/themes` -> ThemeScan
- `GET  /api/workspaces/{id}/news` -> NewsSignals
- `GET  /api/workspaces/{id}/filing-watch` -> FilingWatch
- `POST /api/workspaces/{id}/refresh` -> WorkspaceOverview (re-ingest latest + re-run analysis)
- `POST /api/workspaces/{id}/comps/auto` -> ComparableCompany[] (SIC auto-peer)

## Formulas (verified against live SEC data)

Use latest fiscal year = `t` (last of `years`), prior = `t-1`. `by_year[t]` etc.

**Altman Z″ (private/non-mfg — PRIMARY, fully XBRL, HIGH reliability):**
- X1=(current_assets−current_liabilities)/assets; X2=retained_earnings/assets;
- X3=EBIT/assets where EBIT=operating_income (fallback net_income+tax+interest);
- X4=equity/total_liabilities. Z″=6.56·X1+3.26·X2+6.72·X3+1.05·X4.
- Bands: >2.6 safe, 1.1–2.6 grey, <1.1 distress. Z″<1.1 → red flag `debt_liquidity` severity high(7).

**Piotroski F-score (0–9, needs t & t-1):** ROA_t>0; CFO_t>0; ΔROA>0; CFO_t>NetIncome_t (accrual);
Δleverage (ltd/assets) down; Δcurrent-ratio up; shares_out_t ≤ shares_out_{t-1} (no dilution);
Δgross-margin up; Δasset-turnover (rev/assets) up. F≤2 → red flag `financial` medium/high(6); F≥8 = positive note.
Degrade to F/8 if shares unavailable.

**Beneish M-score (t & t-1):** DSRI=(recv/rev)_t/(recv/rev)_{t-1}; GMI=GM_{t-1}/GM_t; AQI=[1−(current_assets+ppe_net)/assets]_t/[…]_{t-1};
SGI=rev_t/rev_{t-1}; DEPI=[da/(da+ppe_net)]_{t-1}/[…]_t (**suppress if da None**); SGAI=(sga/rev)_t/(sga/rev)_{t-1};
LVGI=[(ltd+ltd_current+current_liabilities)/assets]_t/[…]_{t-1}; TATA=(net_income_t−cfo_t)/assets_t.
M=−4.84+0.92·DSRI+0.528·GMI+0.404·AQI+0.892·SGI+0.115·DEPI−0.172·SGAI+4.679·TATA−0.327·LVGI.
M>−1.78 → elevated manipulation likelihood → red flag `financial` medium(6). If DEPI suppressed, compute
M without it and mark `note: "DEPI omitted (D&A untagged)"`.

**Accruals ratio (Sloan):** (net_income − cfo) / assets. High positive → lower earnings quality.

**QoE metrics:** net_working_capital=current_assets−current_liabilities; DSO=receivables/rev·365;
DIO=inventory/cogs·365; DPO=payables/cogs·365; cash_conversion_cycle=DSO+DIO−DPO;
FCF=cfo−capex; cash_conversion=cfo/net_income (or FCF/net_income); interest_coverage=EBIT/interest (n/a if interest None);
EBITDA=operating_income+da (n/a, EBIT-only, if da None); net_debt=(ltd+ltd_current+short_debt+…)−cash;
leverage_nd_ebitda=net_debt/EBITDA (n/a if EBITDA None). Each a QoEMetric with unit + commentary.

**Valuation:** WACC: risk_free = latest FRED DGS10 (via fred_service); equity_risk_premium=0.05 (assumption);
beta=1.1 (assumption, labeled); cost_of_equity=risk_free+beta·erp; cost_of_debt=risk_free+0.02 (spread);
tax_rate=0.21; debt_weight=net_debt/(net_debt+equity_proxy); WACC=we·coe+wd·cod·(1−tax). DCF-lite: FCF base
grown at `growth` (default 0.05) for 5y + terminal (Gordon, terminal_growth 0.025), discounted at WACC → EV.
LBO: entry_ev=entry_multiple·EBITDA; entry_debt=leverage·EBITDA; entry_equity=entry_ev−entry_debt;
project EBITDA at ebitda_cagr for hold_years; exit_ev=exit_multiple·exit_EBITDA; assume debt paid down by
cumulative FCF (simple: hold debt flat or pay from FCF proxy); exit_equity=exit_ev−exit_debt;
MOIC=exit_equity/entry_equity; IRR=MOIC^(1/hold_years)−1. Sensitivity grid over entry_multiples×exit_multiples.
LABEL every assumption in `assumptions[]`. Mark n/a cleanly when EBITDA is None.

## SEC feeds
- **events**: `edgar_client.get_submissions(cik)` → recent `form`/`filingDate`/`items`/`accessionNumber`/`primaryDocument`
  parallel arrays; decode 8-K item codes (2.02 results, 1.01/1.02 material agreement, 2.01 acquisition/disposition,
  4.01 auditor change, 4.02 **non-reliance/restatement → significant=true, critical flag**, 5.02 exec departure, 1.05 cyber incident, etc.).
- **insiders**: submissions form=="4"; fetch the ownership XML (`.../<accnodash>/form4.xml` or the primaryDocument) and
  parse `<rptOwnerName>`, `<officerTitle>`, `<transactionShares><value>`, `<transactionPricePerShare><value>`,
  `<transactionAcquiredDisposedCode><value>` (A=buy, D=sell). Summarize last ~90 days. Cluster selling → optional flag.
- **themes**: SEC EFTS `https://efts.sec.gov/LATEST/search-index?q="<phrase>"&ciks=<cik10>` (keyless, UA). Run a fixed
  red-flag theme set: "going concern", "material weakness", "restatement", "impairment", "customer concentration",
  "goodwill impairment". Each theme → hit count + a few hits (form/date/url). Cite by adsh.
- **news** (GDELT): `https://api.gdeltproject.org/api/v2/doc/doc?query=<company> sourcelang:english&mode=artlist&format=json&maxrecords=15&sort=datedesc`.
  Label clearly as unverified media (NOT evidence-table). articles[]: title,url,domain,seendate.
- **filing-watch**: compare `edgar_client.recent_filings(cik, ("10-K","10-Q","8-K"), N)` newest filing_date vs. the max
  filing_date already stored in the workspace's `Filing` rows → has_new + the new ones.
- **auto-comps**: `submissions.sic`/`sicDescription`; find same-SIC public filers. Simplest keyless approach: use EFTS or a
  small SIC→peer heuristic; OR reuse `edgar_client` company list filtered by SIC if available. Add as ComparableCompany rows
  (real XBRL via existing `financial_benchmark_service.add_comps_by_ticker`) and return the comps. Keep it best-effort;
  if SIC peer discovery is unreliable, return the few you can resolve and note it.
