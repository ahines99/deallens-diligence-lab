# DealLens API Contract (single source of truth)

This document defines the data model and the exact HTTP API shapes shared by the FastAPI backend
(`apps/api`) and the Next.js frontend (`apps/web`). Both sides MUST match these shapes. TypeScript
mirrors live in `apps/web/src/lib/types.ts`; the API client in `apps/web/src/lib/api.ts`.

All IDs are UUID strings. All timestamps are ISO-8601 strings (UTC). Money uses the declared currency
in raw units (e.g. `55000000`). Rates are decimals (`0.08` = 8%); signed growth and margin fields may
be negative, and each Pydantic field defines its exact bounds.

Base URL: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). All app routes are under `/api`.

---

## Enums

- `deal_type`: `buyout` | `growth_equity` | `private_credit` | `public_equity` | `govcon` | `software_platform`
- `workspace_status`: `draft` | `in_progress` | `complete`
- `target_type`: `public_company` | `private_company` | `synthetic_private` (legacy fixtures only)
- `severity`: `low` | `medium` | `high` | `critical`
- `priority`: `low` | `medium` | `high`
- `claim_type`: `fact` | `calculation` | `inference` | `assumption`
- `memo_type`: `ic_memo` | `bear_case`
- `risk_category` (slug): `customer_concentration` | `supplier_concentration` | `demand_weakness` |
  `margin_pressure` | `debt_liquidity` | `legal_regulatory` | `cyber_security` | `integration_ma` |
  `ai_tech_disruption` | `govcon_risk`
- `workstream`: `commercial` | `product_technology` | `financial` | `customer` | `market` |
  `legal_regulatory` | `cybersecurity` | `ai_data` | `management` | `govcon`

---

## Objects (response shapes)

### Workspace
```json
{ "id","name","organization_id"|null,"target_id"|null,"deal_type","investment_question","status",
  "data_classification","external_llm_allowed","build_status","build_step"|null,"build_error"|null,
  "created_at","updated_at" }
```
`data_classification` is `public`|`internal`|`confidential`|`restricted`; `external_llm_allowed` gates
external LLM processing (always false for `restricted`). `build_status`/`build_step`/`build_error` mirror
the async ingestion progress (`ready`|`building`|`failed`).

### Target (real, from SEC EDGAR XBRL for public companies)
```json
{ "id","name","target_type","ticker"|null,"cik"|null,"sector","description",
  "revenue"|null,"revenue_growth"|null,"gross_margin"|null,"operating_margin"|null,
  "net_income"|null,"net_margin"|null,"rnd_pct"|null,"rule_of_40"|null,
  "cash"|null,"total_debt"|null,"headcount"|null,"fiscal_year_end"|null,
  "data_source","is_synthetic","created_at","updated_at" }
```

### Filing
```json
{ "id","workspace_id","company_name","ticker"|null,"cik"|null,"form_type","filing_date",
  "accession_number"|null,"document_url"|null,"section_count","is_synthetic","created_at" }
```

### ComparableCompany (real peers, from SEC XBRL; market multiples omitted)
```json
{ "id","workspace_id","ticker","company_name","sector","business_description",
  "revenue"|null,"gross_margin"|null,"operating_margin"|null,"net_margin"|null,
  "revenue_growth"|null,"rnd_pct"|null,"market_cap"|null,"enterprise_value"|null,
  "ev_revenue_multiple"|null,"notes","data_source","is_illustrative" }
```
`market_cap`/`enterprise_value`/`ev_revenue_multiple` are null (no free market-data source).

### Evidence
```json
{ "id","workspace_id","ref","claim","claim_type","source_name","source_type","source_url"|null,
  "source_date"|null,"source_section"|null,"evidence_text","confidence","agent_name","created_at" }
```
`ref` is a stable human key like `"EV-001"`. `confidence` is a decimal in `[0,1]`.

### RiskFinding
```json
{ "id","workspace_id","risk_category","risk_category_label","title","finding","severity",
  "severity_score","likelihood","confidence","evidence_ref"|null,"follow_up_question",
  "workstream_owner","created_at" }
```
`severity_score` is 1–10. `risk_category_label` is the human label. `evidence_ref` points to an Evidence `ref`.

### DiligenceQuestion
```json
{ "id","workspace_id","workstream","workstream_label","question","rationale","priority",
  "evidence_ref"|null,"created_at" }
```

### DiligencePlan
```json
{ "workspace_id","investment_question","summary",
  "workstreams": [ { "workstream","workstream_label","objective",
                     "key_questions": [string], "evidence_needed": [string],
                     "status": "planned"|"in_progress"|"complete" } ],
  "generated_at" }
```

### FinancialBenchmark
```json
{ "workspace_id","target_name","peer_count","summary",
  "metrics": [ { "key","label","unit"("pct"|"x"|"usd"|"ratio"),
                 "target_value"|null,"peer_median"|null,"peer_min"|null,"peer_max"|null,
                 "assessment": "above"|"in_line"|"below"|"n/a","commentary" } ],
  "notes": [string], "generated_at" }
```

### Memo
```json
{ "id","workspace_id","memo_type","title","markdown_content","created_at","updated_at" }
```

### RedTeam
```json
{ "id","workspace_id","bear_case_markdown","summary",
  "unsupported_claims": [ { "claim","why_weak","recommended_action" } ],
  "missing_evidence": [ { "item","why_it_matters","workstream" } ],
  "high_priority_questions": [ { "workstream","workstream_label","question","rationale","priority" } ],
  "created_at" }
```

### WorkspaceOverview (aggregate for the workspace detail page)
```json
{ "workspace": Workspace, "target": Target|null,
  "counts": { "filings","comps","risks","questions","evidence" },
  "artifacts": { "plan": bool, "risks": bool, "questions": bool, "ic_memo": bool, "bear_case": bool },
  "top_risks": [RiskFinding] }
```

---

## Endpoints

| Method | Path | Body → Returns |
|---|---|---|
| GET  | `/api/health` | → `{ "status":"ok","llm_mode","database","database_status","schema_management","demo_mode" }` |
| GET  | `/metrics` | → Prometheus text exposition (v0.0.4, `text/plain`): `http_requests_total` counters + `http_request_duration_seconds` histogram, labeled by `method`/`path` (low-cardinality template)/`status`. Public/unauthenticated scrape target. Every response carries an `X-Request-ID` (honored if supplied, else generated) for end-to-end correlation |
| PATCH | `/api/workspaces/{id}/governance` | `{data_classification?,external_llm_allowed?}` (at least one) → `Workspace` (owners/admins only; a `restricted` class cannot enable external LLM) |
| POST | `/api/workspaces` | `{ticker?,name?,deal_type,investment_question?}` → `Workspace` (a `ticker` resolves synchronously — unknown ticker → 404 — then ingestion + analysis run in the background; poll `build-status`) |
| GET  | `/api/workspaces/{id}/build-status` | → `{workspace_id,status:"ready"\|"building"\|"failed",step,error,ticker}` |
| GET  | `/api/workspaces/{id}/build-events` | `text/event-stream` of live build progress: emits a `data: {build-status}` frame on each status/step change until a terminal `ready`/`failed` (or a bounded-duration `timeout` frame). Tenant-guarded; `build-status` polling is the fallback |
| POST | `/api/workspaces/{id}/build/retry` | re-arms a `failed` build and re-runs it (409 unless failed) |
| GET  | `/api/workspaces` | → `Workspace[]` (each carries `build_status`/`build_step`/`build_error`) |
| GET  | `/api/workspaces/{id}` | → `WorkspaceOverview` |
| POST | `/api/workspaces/{id}/qa` | `{question}` → `FilingsQA` (deterministic hybrid retrieval — BM25 fused with local keyless embeddings via reciprocal-rank fusion, falling back to BM25 when a workspace has no embeddings — over ingested filing sections; strictly extractive cited answer or explicit abstention; response `method` is `extractive_hybrid_rrf` or `extractive_bm25`) |
| GET  | `/api/workspaces/{id}/memo/faithfulness` | → runtime report per memo document: citation counts, unresolved `EV-###` refs, uncited numeric sentences |
| GET  | `/api/model-ops/prompt-manifest` | → `{prompts:[{prompt_id,prompt_version,prompt_hash,model}]}` (G10 versioned, SHA-256-hashed prompt registry for reproducible LLM ops; the memo-polish `prompt_hash` is also bound into each LLM-touched sealed `AnalysisRun`'s `output_summary.prompt_manifest`) |
| GET  | `/api/model-ops/quality` | → `{generated_at, judge_evals, retrieval_metrics, calibration, prompts, extraction_comparison, prompt_ab}` (G56 model-quality dashboard). EVERY section carries `{status: "available"\|"unavailable", note}` — absent data reads `unavailable` with a reason, never fabricated zeros. `judge_evals` aggregates G05 faithfulness globally by model/prompt; `retrieval_metrics` reads the committed G03 baseline; `calibration` reports the G06 thresholds + study path; `prompts` = the registry manifests; `extraction_comparison` (G79) = the newest persisted LLM-vs-scanner comparison `{workspace_id, generated_at, both, llm_only, scanner_only}`; `prompt_ab` (G81) = newest A/B report per registered prompt. Session-only surface (no API-key scope), like prompt-manifest |
| POST | `/api/workspaces/{id}/extraction-comparison` | → G79: runs `compare_with_scanner` (LLM extractor vs deterministic scanner on the same chunks) and PERSISTS the result as an append-only `extraction_comparison` ArtifactVersion feeding the quality dashboard. Consent-gated like every LLM path: `{"status":"not_run", reason}` in mock/no-consent/no-key with nothing persisted; a live run that verifies zero findings IS persisted (real data). In the G58 LLM quota set |
| POST | `/api/model-ops/prompt-ab` | `{prompt_id, candidate_template}` → G81: judges the REGISTERED template (A) vs the candidate (B) over the committed golden set with the existing judge; `{status:"completed", a:{prompt_version,prompt_hash,faithful_rate,judged}, b:{prompt_hash_candidate,...}, winner}`; persisted per prompt in blob storage (history capped at 20). 422 unknown prompt / blank candidate; `not_run` provenance in mock. **Promotion convention (the eval gate): a registered template may only change in a PR carrying a winning A/B report — the registry hash change is the tamper signal, the report is the evidence.** In the G58 LLM quota set |
| GET  | `/api/workspaces/{id}/judge-evals` | → `{total,faithful,faithful_rate,groups:[{model_version,prompt_version,count,faithful,faithful_rate,mean_score}]}` (G05 persisted LLM-as-judge faithfulness quality view, grouped by model/prompt) |
| POST | `/api/auth/demo` | → `SessionToken` for a guest identity in the shared Demo Sandbox org (403 unless `DEMO_MODE=true`; rate-limited) |
| GET  | `/api/auth/oidc/login` | → `{authorize_url, state}` — optional OIDC SSO (G48); redirect the browser to `authorize_url`. **404 when `OIDC_ENABLED=false`** (password auth stays the default) |
| GET  | `/api/auth/oidc/callback?code=&state=` | → `SessionToken` — validates `state` (single-use, TTL-bounded, minted by `/login` — unknown/replayed → 401), exchanges `code` for tokens (httpx), requires `iss`/`aud`/`exp`/`nonce` claims (absent → 401; the nonce must echo the one sent on `/login`), maps the IdP role claim (`OIDC_ROLE_CLAIM`) through `OIDC_ROLE_MAP` to a membership role (unmapped/missing → `viewer`), find-or-creates the user by verified email, provisions a membership into `OIDC_ORGANIZATION_SLUG` (an existing membership keeps its role), and issues a normal revocable session. **404 when disabled.** Rate-limited like the other auth endpoints. Security: the `id_token` signature is **not** verified — production must verify against the issuer JWKS |
| GET  | `/api/memberships/{id}/permissions` | → `{membership_id, role, role_defaults:[…], overrides:[{capability,granted}], effective:[…]}` — fine-grained capability view (G49; owners/admins only) |
| PUT  | `/api/memberships/{id}/permissions` | `{capability, granted}` → same shape — grant (`granted=true`) or revoke (`granted=false`) one capability for a membership (owners/admins only; unknown capability → 400) |
| —    | Fine-grained permissions (G49) | Deny-by-default capability matrix layered over the four coarse roles. Catalog: `workspace:read/write`, `underwriting:read/write/approve`, `ic:decide`, `governance:manage`, `member:manage`, `apikey:manage`, `organization:manage`. Role defaults reproduce the coarse behavior — viewer=read-only, member=read+author (no approve/decide/admin), admin=most, owner=all (`organization:manage` is owner-only). `effective = role_defaults ± per-membership grants/revokes`. Applied via `require_capability`: `underwriting:approve` gates the QoE decision endpoint, `ic:decide` gates the IC decision endpoint; the viewer-read-only middleware remains a coarse backstop. Gated by `PERMISSION_MATRIX_ENABLED` (default on) |
| POST | `/api/organizations/{id}/api-keys` | `{name,scopes:[…],expires_at?}` → `{api_key:ApiKey, plaintext_key}` — mints a scoped `dlk_…` key (org owners/admins only). The `plaintext_key` is returned **once**; only its SHA-256 digest is stored. Unknown scopes → 400. Scope catalog: `read:workspaces`, `read:filings`, `read:financials`, `read:underwriting`, `write:underwriting` |
| GET  | `/api/organizations/{id}/api-keys` | → `ApiKey[]` (no secrets; owners/admins only) |
| POST | `/api/api-keys/{id}/revoke` | → `ApiKey` (idempotent; owners/admins of the key's org). A revoked or expired key authenticates nowhere (401) |
| —    | Programmatic auth (`Authorization: Bearer dlk_…`) | resolves to a member principal scoped to the key's organization; the tenant guard still applies, and the granted scopes gate protected routes (e.g. `GET /underwriting/cases` needs `read:underwriting`, `POST` needs `write:underwriting`) → 403 on insufficient scope. Typed clients are generated from `/openapi.json` (see `apps/api/scripts/generate_client.md`) |
| GET  | `/api/organizations/{id}/quota-usage` | → `{organization_id, buckets:[{name,used,limit,window_seconds,remaining}], llm_spend}` — current per-organization quota usage (G39). Buckets: `requests` (per-minute), `builds` (per-hour), and `llm` (per-hour, G58 — counts POSTs to LLM-capable routes ONLY when `LLM_MODE=live`; a tripped quota 429s with "deterministic endpoints remain available"); `limit=0` means unlimited (`remaining=null`). G80: `llm_spend = {window_hours:24, total_calls, input_tokens, output_tokens, by_model:[{model,calls,input_tokens,output_tokens}]}` — token usage captured at the provider seam per live call, attributed via the request's org context (background/untagged calls appear only in global rollups, never guessed onto a tenant). Org-scoped: a cross-tenant read is 404 |
| —    | Per-organization quotas (G39) | Every authenticated request counts toward its org's per-minute `requests` quota (`ORG_REQUEST_QUOTA_PER_MINUTE`, default 600); SEC-bound build endpoints (`POST /api/workspaces`, `/workspaces/{id}/build/retry`, `/refresh`, `/sec/ingest`) also count toward the per-hour `builds` quota (`ORG_BUILD_QUOTA_PER_HOUR`, default 60). API-key callers count toward their org. Over-quota → 429 with `Retry-After`; `0` = unlimited. Layers over the demo per-IP build throttle |
| POST | `/api/examples/private-deal` | → `{organization_id,fund_id,deal_id,workspace_id,deal_code,import_status,open_exceptions}` (loads the bundled fictional private deal through the real import/governance pipeline; QoE adjustments stay `proposed`) |
| GET  | `/api/examples/templates` | → `[{name,description}]` |
| GET  | `/api/examples/templates/{name}` | → file download (financials CSV + example data-room documents) |
| GET  | `/api/workspaces/{id}/target` | → `Target` (404 if none) |
| POST | `/api/workspaces/{id}/target` | `Target`-create fields → `Target` |
| GET  | `/api/sec/search?q=` | → `[{ "cik","ticker","name" }]` (EDGAR company search; mock fixtures offline) |
| POST | `/api/sec/ingest` | `{workspace_id,ticker?,cik?,form_types?:[string],limit?:int}` → `Filing[]` |
| GET  | `/api/workspaces/{id}/filings` | → `Filing[]` |
| POST | `/api/workspaces/{id}/comps` | `{tickers?:[string], comps?:ComparableCompany[]}` → `ComparableCompany[]` (tickers fetched from SEC XBRL; re-runs analysis so the memo benchmark updates) |
| GET  | `/api/workspaces/{id}/comps` | → `ComparableCompany[]` |
| GET  | `/api/workspaces/{id}/benchmark` | → `FinancialBenchmark` |
| GET  | `/api/workspaces/{id}/comps/similarity` | `?top_n` → `CompSimilarity` (embedding-similarity peer ranking of business descriptions, side-by-side with the SIC-code method; `disagreements.{embedding_only,sic_only}` flag where the two methods diverge; `available:false` with no fabricated similarity when descriptions are missing) |
| POST | `/api/workspaces/{id}/plan/generate` | → `DiligencePlan` |
| GET  | `/api/workspaces/{id}/plan` | → `DiligencePlan` (404 if not generated) |
| POST | `/api/workspaces/{id}/risks/generate` | → `RiskFinding[]`. G52: when the workspace consents to external LLM (and is not `restricted`) in live mode, text findings come from the LLM extractor with VERBATIM-span verification (a finding is dropped unless its quote appears character-for-character, whitespace-normalized, in the source chunk); otherwise the deterministic signal-phrase scanner runs. The sealed run records `output_summary.risk_extraction = {engine: "llm"\|"deterministic", reason, manifest, proposed, verified, rejected}` |
| GET  | `/api/workspaces/{id}/risks` | → `RiskFinding[]` |
| POST | `/api/workspaces/{id}/questions/generate` | → `DiligenceQuestion[]` |
| GET  | `/api/workspaces/{id}/questions` | → `DiligenceQuestion[]` |
| POST | `/api/workspaces/{id}/memo/generate` | → `Memo` (memo_type=ic_memo) |
| GET  | `/api/workspaces/{id}/memo` | → `Memo` (ic_memo; 404 if not generated) |
| POST | `/api/workspaces/{id}/red-team/generate` | → `RedTeam` (also persists a bear_case Memo) |
| GET  | `/api/workspaces/{id}/red-team` | → `RedTeam` (404 if not generated) |
| GET  | `/api/workspaces/{id}/evidence` | → `Evidence[]` |
| GET  | `/api/workspaces/{id}/trends` | → `FinancialTrends` (multi-year XBRL; 404 if unavailable) |
| GET  | `/api/workspaces/{id}/financials/quarterly` | → `QuarterlyFinancials` (last 8 discrete/derived 10-Q quarters + per-metric TTM; TTM is null-with-reason unless four contiguous quarters exist — Q4 may be derived as FY−(Q1+Q2+Q3), labeled `fy_minus_q123`; workspaces ingested before this feature return `source_status:"unavailable"` until refreshed) |
| GET  | `/api/workspaces/{id}/financials/segments` | → `SegmentRevenue` (per-segment revenue trend from dimensional XBRL facts on a reporting axis, e.g. `StatementBusinessSegmentsAxis`; `source_status:"available"` when members reconcile to consolidated, `"partial"` when they don't fully reconcile (untagged Other/eliminations), `"unavailable"` when companyfacts is consolidated-only — standard SEC company facts publish no dimensional facts, so segment splits are never fabricated; workspaces ingested before this feature return `"unavailable"` until refreshed) |
| GET  | `/api/workspaces/{id}/debt-maturities` | → `DebtMaturitySchedule` (long-term-debt principal "maturity wall" from us-gaap `LongTermDebtMaturitiesRepaymentsOfPrincipal...` per-year XBRL concepts; `schedule:[{bucket,amount,source_concept,period_end}]` in year order Y1..Y5/thereafter; `source_status:"available"` when every bucket is tagged, `"partial"` when some are and untagged buckets are listed in `missing_buckets` and OMITTED from the schedule (never zero-filled or interpolated), `"unavailable"` when no maturity concepts are tagged; `total_scheduled` sums only tagged buckets; workspaces ingested before this feature return `"unavailable"` until refreshed) |
| GET  | `/api/workspaces/{id}/macro` | → `MacroOverlay` (FRED series relevant to the target's sector) |
| POST | `/api/workspaces/{id}/govcon` | `{recipient_name?}` → `GovConProfile` (fetches USAspending federal awards, re-runs analysis; 502 on upstream failure) |
| GET  | `/api/workspaces/{id}/govcon` | → `GovConProfile` (404 if not fetched) |
| POST | `/api/workspaces/{id}/governance-profile` | → `GovernanceProfile` (fetches the target's most recent DEF 14A proxy, parses the Summary Compensation Table into NEO rows `{name,title,salary,bonus,stock_awards,total}` — unextractable values stay `null`, never imputed — and runs governance red-flag heuristics `{flag,label,present,evidence}` for staggered/classified board, dual-class shares, combined CEO/Chair, and poison pill; persisted and re-run on demand; `source_status` available/partial/unavailable, an EDGAR outage or missing proxy stored as `unavailable` — never false-clean. Distinct from PATCH `/governance` below) |
| GET  | `/api/workspaces/{id}/governance-profile` | → `GovernanceProfile` (404 if not yet built) |
| GET  | `/api/workspaces/{id}/forensics` | → `Forensics` (Altman Z″, Piotroski F, Beneish M, accruals + QoE metrics; from XBRL). Carries `fiscal_diagnostics`: `[]` = all derived metrics used same-period operands, `[{metric,period_a,period_b,severity,detail}]` = mixed-period operands flagged, `null` = not computable (no stored source points) |
| GET  | `/api/workspaces/{id}/valuation` | → `Valuation` (WACC from FRED, DCF-lite; assumptions labeled) |
| POST | `/api/workspaces/{id}/lbo` | `LboInputs` → `LboResult` (IRR/MOIC + entry×exit sensitivity grid) |
| GET  | `/api/workspaces/{id}/events` | → `EventTimeline` (8-K item-code material events; 4.02 flagged significant) |
| GET  | `/api/workspaces/{id}/insiders` | → `InsiderActivity` (Form 4 buys/sells) |
| GET  | `/api/workspaces/{id}/insider-patterns` | → `InsiderPatterns` (clustered buy/sell windows, 10b5-1 plan summary, officer/director/10%-owner split; same Form 4 feed + source_status) |
| GET  | `/api/workspaces/{id}/institutional-ownership` | → `InstitutionalOwnership` (13F holder-concentration: HHI, top-5 share, holder count. `scope=manager_portfolio` when the target itself files 13F-HR — reports ITS holdings' concentration; `scope=not_applicable` otherwise — keyless reverse holder-lookup by CUSIP is unavailable) |
| GET  | `/api/workspaces/{id}/activist-stakes` | → `ActivistStakes` (SC 13D/13G filings about the target, classified activist=13D vs passive=13G, as timeline events; filer/percent best-effort from cover page) |
| GET  | `/api/workspaces/{id}/themes` | → `ThemeScan` (SEC full-text red-flag theme scan) |
| GET  | `/api/workspaces/{id}/news` | → `NewsSignals` (GDELT media — unverified, not evidence) |
| GET  | `/api/workspaces/{id}/filing-watch` | → `FilingWatch` (new filings since last analysis) |
| GET  | `/api/workspaces/{id}/signals-overview` | → `SignalsOverview` (G18; carryover F55): one consolidated screen aggregating events/insiders/themes/news into `sections:[{kind,source_status,source_error,summary,items}]` — each section carries its OWN `source_status`, so a partial/unavailable feed is shown explicitly and never merged into a false-clean empty; `overall_status` is the honest roll-up (clean only when every feed is available) |
| GET  | `/api/workspaces/{id}/export-bundle` | → `application/zip` StreamingResponse (G45): a verifiable bundle containing the IC memo rendered to PDF (`ic-memo.pdf`), an evidence appendix (`evidence-appendix.csv`, every `EV-###` with claim/source/text) and `manifest.json` `{workspace_id,generated_at,files:[{name,sha256,bytes}],bundle_sha256,memo_sha256,evidence_count}`. Response carries `X-Bundle-SHA256`; the PDF is rendered deterministically (reportlab invariant) and `bundle_sha256` excludes the timestamp, so identical inputs yield a stable digest. 404 if no IC memo has been generated |
| POST | `/api/workspaces/{id}/export-bundle/verify` | multipart `file?` → `{valid,checks:[{name,expected,actual,passed}]}` (G45): re-reads a bundle (uploaded `file`, or a freshly regenerated one when omitted), recomputes each file's SHA-256 and the `bundle_sha256`/`memo_sha256` rollups, and confirms they match the embedded manifest — offline tamper detection mirroring `GET /api/ic-exports/{id}/verification` |
| POST | `/api/workspaces/{id}/refresh` | → `WorkspaceOverview` (re-ingest latest + re-run analysis) |
| POST | `/api/workspaces/{id}/comps/auto` | → `ComparableCompany[]` (SIC-based auto peer discovery) |

Wave-2 object shapes (`Forensics`, `Valuation`, `LboInputs`/`LboResult`, `EventTimeline`, `InsiderActivity`,
`ThemeScan`, `NewsSignals`, `FilingWatch`) are defined in `apps/web/src/lib/types.ts`. Forensics/valuation
compute from `target.financials["forensic_inputs"]` (XBRL, extracted at ingestion); events/insiders/themes/news
fetch live (SEC EFTS / Form 4 / GDELT, keyless). All degrade to `n/a` rather than imputing.

### FinancialTrends / MacroOverlay / GovConProfile
```json
FinancialTrends: { "workspace_id","target_name","years":[string],
  "rows":[{ "year","revenue"|null,"gross_margin"|null,"operating_margin"|null,"net_margin"|null,"rnd_pct"|null }],
  "revenue_cagr"|null,"generated_at" }
MacroOverlay: { "workspace_id","target_name","sector","commentary",
  "series":[{ "series_id","label","unit","note","latest_value","latest_date","yoy_change"|null,
              "points":[{ "date","value" }] }],"generated_at" }
GovConProfile: { "id","workspace_id","recipient_name","total_obligations","award_count",
  "top_agency"|null,"top_agency_pct"|null,
  "agency_concentration":[{ "agency"|null,"amount","pct"|null }],
  "top_awards":[{ "award_id","recipient","agency","sub_agency","amount"|null,"description","pop_end"|null,"pop_start"|null }],
  "recompete":{ "count","value","awards":[{ "award_id","agency","amount"|null,"pop_end"|null }] },"created_at" }
```

Notes:
- `generate` endpoints are idempotent: they (re)build the artifact from mock seed (or live LLM) and
  upsert. Calling GET before generate returns 404 with `{ "detail": "..." }`.
- All POST/GET that reference `{id}` return 404 if the workspace doesn't exist.
- Generating risks/questions/memo/red-team also creates the Evidence rows they cite.

## Frontend pages (App Router)

The app under `apps/web/src/app` currently ships ~32 routes: seven top-level pages plus ~25 workspace
tabs.

```
# Top level
/                                     landing + disclaimer + "New workspace"
/login                                sign in (same-origin session bridge)
/register                             first-user bootstrap + self-registration
/pipeline                             deal pipeline across the organization
/portfolio                            portfolio command center (KPIs, funnel, exposure, health)
/workspaces                           list of workspaces
/workspaces/new                       create form (enter a ticker → real SEC ingest)

# Workspace detail — /workspaces/[workspaceId]/*
/workspaces/[workspaceId]             overview: plan, progress, top risks, generate actions
/workspaces/[workspaceId]/target      target profile
/workspaces/[workspaceId]/filings     filings table + SEC ingest
/workspaces/[workspaceId]/comps       comps table + financial benchmark
/workspaces/[workspaceId]/risks       red-flag matrix
/workspaces/[workspaceId]/questions   diligence questions by workstream
/workspaces/[workspaceId]/qa          "Ask the filings" — cited extractive Q&A (BM25)
/workspaces/[workspaceId]/memo        IC memo viewer + faithfulness report
/workspaces/[workspaceId]/red-team    bear case + unsupported claims + missing evidence
/workspaces/[workspaceId]/evidence    evidence & audit table
/workspaces/[workspaceId]/trends      multi-year XBRL trends
/workspaces/[workspaceId]/macro       FRED macro overlay
/workspaces/[workspaceId]/govcon      GovCon federal-award profile
/workspaces/[workspaceId]/forensics   QoE / forensics (Altman, Piotroski, Beneish, accruals)
/workspaces/[workspaceId]/valuation   WACC / DCF-lite / LBO sensitivity
/workspaces/[workspaceId]/events      8-K material-event timeline
/workspaces/[workspaceId]/insiders    Form 4 insider activity
/workspaces/[workspaceId]/news        GDELT news signals (unverified media)
/workspaces/[workspaceId]/data-room   private-deal document room
/workspaces/[workspaceId]/qoe         QoE adjustment ledger + bridge
/workspaces/[workspaceId]/underwriting versioned LBO/operating model + cases
/workspaces/[workspaceId]/stress      sensitivity / reverse-stress / Monte Carlo
/workspaces/[workspaceId]/execution   deal execution (gates, workstreams, tasks)
/workspaces/[workspaceId]/intelligence document intelligence + SEC comparisons
/workspaces/[workspaceId]/ic          IC packet assembly, readiness, decisions
```

Components: `WorkspaceCard`, `TargetProfile`, `FilingTable`, `CompsTable`, `RiskMatrix`,
`QuestionList`, `MemoViewer`, `RedTeamViewer`, `EvidenceTable`, `ClaimBadge`, `SourceCitation`,
plus shared UI in `components/ui/`. Charts use Recharts. Every claim surfaces a `ClaimBadge`
(fact/calculation/inference/assumption) and links to its evidence `ref`.

---

## Wave 3A/3B contracts

The authoritative Pydantic and TypeScript shapes for the institutional workbench live in:

- `apps/api/src/schemas/underwriting_data.py`
- `apps/api/src/schemas/underwriting_model.py`
- `apps/api/src/schemas/deal_workflow.py`
- `apps/api/src/schemas/deal_intelligence.py`
- `apps/api/src/schemas/integration.py`
- `apps/web/src/lib/types.ts`

Endpoint families:

| Area | Prefix / representative routes |
|---|---|
| Private financials | `/api/workspaces/{id}/underwriting/sources`, `/financial-imports/{csv,xlsx}`, `/financial-facts`, `/reconciliations`, `/import-exceptions` |
| QoE | `/api/workspaces/{id}/underwriting/qoe-adjustments`, `/qoe-bridge` |
| Model | `/api/workspaces/{id}/underwriting/{calculate,cases,case-set,working-capital-peg,valuation-triangulation,sensitivity,reverse-stress}` |
| Monte Carlo LBO | `/api/workspaces/{id}/underwriting/monte-carlo` — seeded driver-distribution simulation; percentile IRR/MoIC bands, reproducible for identical seed + inputs |
| Returns attribution | `/api/workspaces/{id}/underwriting/returns-attribution` — EBITDA growth / multiple change / deleveraging / cross-term bridge; components sum exactly to total value creation |
| Covenant headroom | `POST /api/workspaces/{id}/underwriting/covenant-headroom` (`{assumptions}` → `CovenantHeadroomResult`) — per-covenant, per-period signed headroom (positive = compliant) with `breached` flags and `first_breach_period` at the threshold-crossing quarter |
| Case variance | `POST /api/workspaces/{id}/underwriting/case-variance` (`{management, sponsor}`, each an inline `assumptions` **or** a persisted `case_key`[`+version`] → `CaseVarianceResult`) — line-level management-minus-sponsor deltas with absolute + pct delta, ranked by descending percentage materiality |
| Exit readiness | `POST /api/workspaces/{id}/underwriting/exit-readiness` (`{assumptions}` → `ExitReadinessResult`) — leverage/growth/margin/coverage scorecard with explicit thresholds + a 3/5/7-year hold-period IRR/MoIC grid |
| Football field | `POST /api/workspaces/{id}/underwriting/football-field` (`ValuationTriangulationRequest` → `FootballFieldResult`) — DCF/comps/precedent bars with explicit weights summing to 1 across included methods; a method with no inputs is excluded with a reason, never imputed |
| Driver model | `POST /api/workspaces/{id}/underwriting/driver-model` (`{drivers:[{name,formula,unit?,provenance?}]}` → `DriverModelResult`) — user-defined drivers whose `formula` (RHS only) references other driver names + numeric constants with `+ - * /` and parentheses. Evaluated by a **whitelisted AST walk (no `eval`/`exec`)**; each line returns its `value`, `depends_on`, and provenance (user note + transitive input closure) plus a topological `evaluation_order`. Unsafe formula (call/attribute/subscript/`**`), unknown reference, or a cycle (path named, e.g. `a -> b -> a`) → 422 |
| WC seasonality | `POST /api/workspaces/{id}/underwriting/working-capital-seasonality` (`{monthly_working_capital:[{month:1..12,value}]}` → `WorkingCapitalSeasonalityResult`) — per-month peg (repeated months averaged in place, not a single annual average) with peak/trough month + amplitude over present months; absent months are reported in `missing_months` and `status:"partial"`, never interpolated |
| Recap / bolt-ons | `POST /api/workspaces/{id}/underwriting/recap-boltons` (`{assumptions, events:[{type,period,amount?,incremental_ebitda?,multiple_paid?,funded_by?}]}` → `RecapBoltOnResult`) — overlays `dividend_recap` (debt-funded equity dividend; lifts IRR + exit leverage) and `bolt_on` (adds EBITDA, debt- or equity-funded) on the base case; re-prices sponsor cash flows via `xirr`, reports base-vs-adjusted IRR/MoIC/leverage deltas and Decimal-exact per-event sources/uses. Event period absent from the projection → 422 |
| Identity & sessions | `POST /api/auth/{register,login,demo,logout,switch-organization}` → `SessionToken`; `GET /api/auth/me` → `CurrentIdentity`; `GET`/`POST /api/organizations/{id}/members`, `PATCH /api/memberships/{id}` (org membership admin) |
| Portfolio | `GET /api/organizations/{id}/portfolio` → `PortfolioDashboard` (filters: `search,stage,fund_id,as_of,ic_window_days`); `/portfolio/export.csv` (CSV download); `/portfolio/health` → `PortfolioHealth`; `GET /api/organizations/{id}/fund-construction` → `FundConstructionReport` (per-fund sized exposure by sector/strategy/stage vs. configurable concentration caps `single_sector_max,single_deal_max,single_strategy_max` — breaches carry `{dimension,key,exposure_pct,limit,excess}` — plus a vintage-based linear pacing model `ahead\|on_track\|behind\|unknown`; sizing is committed sponsor equity from each deal's underwriting case, and unsized deals are excluded and reported in `sizing_coverage`, never imputed. Filters: `fund_id,as_of,target_fund_size,investment_period_years,pacing_tolerance,near_breach_ratio`) |
| Activity | `GET /api/organizations/{id}/activity` → `ActivityTimeline` (unified timeline; filters `deal_id,actor_id,category,before,limit`) |
| Notifications | `GET /api/organizations/{id}/notifications` (`?unread_only`) → `Notification[]`; `GET /api/organizations/{id}/notifications/unread-count` → `{organization_id,unread}`; `POST /api/notifications/{id}/read` → `Notification` — an idempotent, dedup-by-`source_audit_event_id` projection of the workflow audit outbox into read-model notifications. A `comment.mentioned` event projects to a **directed** notification (`recipient_user_id` set to the mentioned member); every other event is organization-wide (`recipient_user_id=null`) |
| Comments (G41) | `POST /api/comments` (`{entity_type,entity_id,body,parent_comment_id?}` → `Comment`) — a threaded comment on any governed artifact (`entity_type` ∈ `risk\|qoe_adjustment\|memo\|ic_packet\|workspace`); `@mentions` in `body` are resolved against active org members (email or email-handle) and stored as `mentions:[user_id]`, each firing a `comment.mentioned` `WorkflowAuditEvent` through the outbox (→ directed notification) plus one `comment.created` event; non-member mentions are ignored. Author is server-derived (auth required); **viewers are read-only → 403**. `GET /api/comments?entity_type=&entity_id=` → `CommentThread[]` (top-level comments each with a `replies[]`), tenant-scoped to the caller's org (cross-org is empty/404). `POST /api/comments/{id}/resolve` → `Comment` (sets `resolved_at`/`resolved_by_user_id`; idempotent) |
| Share links (G44) | `POST /api/workspaces/{id}/share-links` (`{label?,scope?=read_only,expires_at?}` → `{share_link, token}`) — mints an opaque `dsh_…` token returned **once**; only its SHA-256 digest is stored. `GET /api/workspaces/{id}/share-links` → `ShareLink[]` (no secrets). `POST /api/share-links/{id}/revoke` → `ShareLink` (idempotent). `GET /api/shared/{token}` → `SharedWorkspaceSnapshot` — **public, session-less** (the token is the authorization): a read-only, **non-confidential** snapshot (workspace identity, target public-company identity, risk findings, counts) that deliberately excludes financial line items, valuation, QoE adjustments, memo bodies, and data-room content. A revoked/expired token → **410**; unknown/malformed → **404**. Management endpoints are workspace-/org-scoped (tenant-guarded; viewers 403) |
| Watchlists (G19) | `POST /api/organizations/{id}/watchlist` (`{ticker?\|cik?,company_name?}`) → `WatchlistEntry` (add/reactivate; ticker resolved to CIK via EDGAR); `GET /api/organizations/{id}/watchlist` → `WatchlistEntry[]`; `DELETE /api/watchlist/{id}` (204); `POST /api/organizations/{id}/watchlist/refresh` → `WatchlistRefreshResult` `{entries_checked,new_filings,events_emitted,unavailable}`. Refresh reads EDGAR submissions per active entry and emits one `watchlist.filing_detected` `WorkflowAuditEvent` through the existing outbox (fan-out to notifications + signed webhooks) for each filing newer than the `last_seen_accession` dedup cursor; a brand-new entry's first refresh only records a baseline (never floods the backlog). A scheduled worker `python -m src.workers.watchlist_refresh --once\|--interval` refreshes all orgs. |
| Deal execution | `/api/organizations`, `/api/funds/{id}/deals`, `/api/deals/{id}/{gates,team,workstreams,milestones,tasks,diligence-requests,ledger}` |
| IC governance | `/api/deals/{id}/ic-packets`, `/api/ic-packets/{id}/{readiness,submit,comments,decisions,exports}`, `GET /api/ic-exports/{id}/verification` → `ExportVerificationResult` (recomputes the manifest hash to detect tampering) |
| Documents/evidence | `/api/deals/{id}/intelligence/{documents,qa,extractions,claims,comparisons,evaluations}`. G53: when the deal's linked workspace consents to external LLM (and is not `restricted`) in live mode, `POST .../extractions` proposes claims via the LLM and mints ONLY deterministically verified ones (quote verbatim in the chunk, whitespace-normalized; the claimed value present inside the quote on digit boundaries — no scale inference); otherwise the pattern extractor runs byte-identically to before. Response shape unchanged (`StructuredClaimOut[]`, claims land `unreviewed` for four-eyes review); per-claim engine provenance rides `extraction_version` (`llm-verified-v1` vs `rules-evidence-v1`), and consent-gated runs emit an `intelligence.claim_extraction` audit event with `{engine, llm: {applied, reason, manifest, proposed, verified, rejected[]}}` |
| SEC changes | `/api/workspaces/{id}/intelligence/sec-comparisons` |
| Risk-factor drift | `GET /api/workspaces/{id}/filings/risk-diff` → `RiskDiffOut` — cross-year Item 1A diff of the two most recent 10-Ks classified `added`/`removed`/`changed` by embedding-cosine alignment (match ≥0.50, unchanged ≥0.98), each with a citation into both filings; `source_status="unavailable"` (never fabricated) when <2 10-Ks or no risk section |
| Cross-corpus Q&A | `POST /api/workspaces/{id}/cross-corpus-qa` (`{question, grounded?: false}` → `CrossCorpusQAOut`) — one extractive, abstaining answer over public filing chunks + (if the workspace links a deal) confidential data-room chunks; every citation labeled `corpus`=`public_filing`\|`confidential_dataroom` with a `confidential` flag; degrades to filings-only, labeled public, when no data room exists. G54: `grounded=true` requests a fail-closed fluency pass (consent resolved server-side: workspace `external_llm_allowed` and not `restricted`; confidential quotes NEVER leave the box otherwise). Response gains `grounded: {applied, reason: applied\|not_eligible\|no_consent\|mock\|no_api_key\|audit_rejected\|error, manifest}\|null`; citations are byte-identical in every path and abstentions are never synthesized |
| Diligence agent (G57) | `POST /api/workspaces/{id}/agent/run` (`{objective, max_steps?: 8 (1..16)}` → `AgentRunOut`) — a budget-capped tool-use loop over a curated allowlist of GOVERNED, read-only/pure-compute workspace tools (overview, filing search, cited Q&A, risks, evidence, saved cases, in-memory underwriting scenarios); the harness scopes every call (the model never chooses the workspace) and the agent cannot write to any governed record. Consent-gated like every LLM path (`not_run` with reason `mock`/`no_consent`/`no_api_key`; zero provider calls). The final answer passes a fail-closed grounding gate — any quantity token or EV-### ref no tool result (or the objective) produced → `status="rejected_ungrounded"`, answer withheld, violations listed. Every executed run seals an append-only `agent_run` ArtifactVersion with the full step transcript, and deal-linked workspaces emit an `agent.run_completed` audit event. Statuses: `completed`\|`rejected_ungrounded`\|`budget_exhausted`\|`error`\|`not_run`. In the G58 LLM quota set |
| Workspace search | `GET /api/workspaces/{id}/search?q=&limit=` → `{query, hits:[{artifact_type, artifact_id, title, snippet, rank}], engine, total}` — one interface over all workspace artifacts (evidence, risk, question, memo, filing, document_chunk), ranked and workspace-scoped; searches the live tables at query time (never stale). `engine` is `sqlite_like` (deterministic tokenized LIKE, whole-word > partial) or `postgresql_tsvector` (`to_tsvector`/`plainto_tsquery`/`ts_rank`); an empty/no-hit query returns `hits:[]` |
| Integrations | `/api/organizations/{id}/webhooks`, `/api/organizations/{id}/webhook-deliveries`, `/api/webhook-deliveries/{id}/send` |
| My reviews (G42) | `GET /api/organizations/{id}/my-reviews?actor_id=` → `{organization_id, actor_id, items:[{plane, id, title, deal_or_workspace, created_at, url_hint}], counts_by_plane:{qoe,claim,diligence,ic_comment}, total}` — one org-scoped queue of items awaiting the signed-in actor across four planes: proposed QoE adjustments, unreviewed structured claims, responded diligence requests, and open blocking IC comments. Each plane honours four-eyes: items the actor proposed/authored/responded to are excluded (they cannot decide them). `actor_id` defaults to the principal; cross-tenant read is 404 |
| Audit explorer (G43) | `GET /api/organizations/{id}/audit-events?actor_id=&entity_type=&entity_id=&since=&until=&limit=` → `WorkflowAuditOut[]` (newest first) — org-level filterable view over the append-only `WorkflowAuditEvent` outbox; `GET /api/organizations/{id}/audit-events/export.csv` streams the same filtered set as CSV with spreadsheet-formula injection neutralized (CWE-1236) on every user-controlled field. Org-scoped: cross-tenant read is 404 / empty |
| Memo redlines (G47) | `GET /api/workspaces/{id}/memo-redline?run_a=&run_b=` → `MemoRedlineOut` `{run_a, run_b, granularity, changed:[{before, after, numeric_change, numbers_added, numbers_removed}], added, removed, numeric_changes, counts, is_empty}` — side-by-side diff of two `AnalysisRun`s' sealed memo content (the linked `ArtifactVersion.content_json['ic_memo_markdown']`, falling back to `content_text`/`output_summary` with the `granularity` stated). Claims are sentence-level; a claim whose numeric-masked skeleton is stable but whose numbers moved is a `changed` entry with `numeric_change` set and the exact tokens listed. Identical runs → empty diff |

Money uses the case currency and rates are decimals. Model case, source, document, claim, analysis-run,
artifact, and IC packet histories are append-only or versioned. Missing/failed information is represented
as missing, partial, stale, failed, or abstained; it is never converted to a clean zero.

All `/api` routes also resolve under `/api/v1`. Webhook payloads are canonical JSON CloudEvents-style
envelopes backed by the workflow audit stream. The signature is lowercase hex HMAC-SHA256 over the ASCII
timestamp, a literal period, and the exact request bytes. Receivers should reject stale timestamps and
deduplicate on `X-DealLens-Delivery`.

See [`WAVE3.md`](./WAVE3.md) for the end-to-end workflow and import/export requirements.
