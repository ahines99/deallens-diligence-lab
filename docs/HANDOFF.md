# DealLens Diligence Lab — Handoff

*Last updated 2026-07-17 at commit `2c65f20` (main, **2 commits ahead of origin — the two
audit-remediation commits `2498feb` and `2c65f20` are unpushed**).*

This document is the orientation guide for anyone taking over or resuming work: what the project
is, how it is built, the rules the code lives by, where everything is, and what remains open.
Deeper references: [architecture.md](architecture.md), [CONTRACTS.md](CONTRACTS.md) (the API
surface, mirrored by the frontend types), [evidence-model.md](evidence-model.md),
[FEATURE_LEDGER.md](FEATURE_LEDGER.md), [ROADMAP-WAVE4.md](ROADMAP-WAVE4.md),
[deploy-demo.md](deploy-demo.md), and `apps/web/DESIGN.md`.

---

## 1. What this is

A **private-equity underwriting, diligence, and investment-committee workbench** built on **real
public data** — SEC EDGAR (XBRL company facts, filings, Form 4, 13F/13D-G, DEF 14A, 8-K), FRED,
USAspending, and GDELT. All sources are keyless; only a descriptive `SEC_USER_AGENT` is required.
Type a ticker and a diligence workspace builds itself live: financials, risk findings, diligence
plan and questions, IC memo, bear case — every material claim backed by an append-only evidence
row.

It is an **independent, non-commercial portfolio project** (started 2026-07-08) by Alex Hines,
who is job-searching for senior AI/data roles in PE / asset management / investment research. The
audience is a technical PE/investing reader: the pitch is *"real data, deterministic models,
governed evidence — never a guess."* Outputs are explicitly **not investment advice**
([disclaimers.md](disclaimers.md)).

Public repo: <https://github.com/ahines99/deallens-diligence-lab> (MIT, CI badge, screenshots in
`docs/images/`).

## 2. The rules the code lives by

These invariants are enforced in code (ORM listeners, fail-closed guards, tests) and every change
is expected to preserve them. They are the project's identity — reviewers *will* probe them.

1. **Never impute.** Missing XBRL concepts degrade to explicit `n/a` / `unavailable` /
   `missing_buckets` — never zero-filled, interpolated, or blended across period gaps (see the
   TTM contiguity rules and debt-maturity extractor in `sec_financials.py`).
2. **Evidence-grounded.** Every material claim cites an `EV-###` `Evidence` row. Evidence,
   `AnalysisRun`, `ArtifactVersion`, `SourceSnapshot`, and underwriting case versions are
   **append-only** — ORM event listeners raise on mutation. Regeneration creates new versions
   with `supersedes_id` chains; frozen artifacts keep resolvable citations forever.
3. **Deterministic first, LLM only as consent-gated polish.** The single LLM path is
   `polish_markdown` (Anthropic, `LLM_MODE=live`), which re-voices prose and **fails closed** via
   `CitationAuditor` if numbers or citations drift. Restricted-classification workspaces forbid
   external LLM entirely. Sealed runs record honest provenance (`llm_polished`, hashed prompt
   manifest, degraded sources).
4. **Honest degradation everywhere.** External feeds report
   `available / partial / unavailable`; a failed fetch is *recorded* as degraded, never inferred
   clean. The frontend mirrors this: server pages use `loadOrUnavailable` /
   `loadWorkspaceDeal` so an API outage renders a warning callout, never clean-empty data.
5. **Tenant isolation without oracles.** Cross-tenant access to any resource returns the *same
   404* as an unknown id (no 403 existence oracle). Identifiers are non-enumerable.
6. **Concurrency authority is the database.** Version allocation is SELECT max+1 *inside* a
   savepoint-retry (`common.insert_versioned`, mirroring `evidence_service.create`); unique
   constraints decide races. Case versions use optimistic CAS (409 on conflict) by design.
7. **Contracts stay in sync.** `docs/CONTRACTS.md` ⇄ backend Pydantic schemas ⇄
   `apps/web/src/lib/types.ts` must mirror each other.

## 3. Stack and layout

Monorepo, **zero exotic runtime dependencies** (a point of pride — Wave 4 added 50 features with
an unchanged lockfile).

```
apps/api      FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic (17 migrations)
              SQLite by default; Postgres via docker-compose (CI runs both)
  src/routers   41 modules — HTTP surface (thin; errors translated to HTTPException)
  src/services  60 modules — ALL business logic lives here (routers stay dumb)
  src/models    30 modules — ORM; append-only guards; unique constraints
  src/schemas   Pydantic contracts (StrictModel, extra="forbid")
  src/agents    Deterministic "analysts" (risk/financial/memo/red-team) + llm_provider
  src/workers   webhooks outbox, durable jobs, demo cleanup (python -m src.workers.<name>)
  src/main.py   app assembly + ALL middleware (auth, tenant guard, rate limits, quotas, CORS)
  tests/        69 files, ~550 test functions; offline-first (live SEC tests auto-skip)
apps/web      Next.js 15 App Router, TS strict, Tailwind tokens, Recharts
  src/app       server-components-first pages; login/register/pipeline/portfolio +
                /workspaces/[id]/<26 tabs> (risks, memo, underwriting, ic, intelligence, …)
  src/components  workbench client components; colocated *.test.tsx (vitest, 24 files)
  src/lib       api.ts (client), serverApi.ts (server-only + loadOrUnavailable),
                types.ts (mirror of CONTRACTS.md)
docs/         contracts, architecture, methodology, deploy runbook, screenshots
docker-compose.yml   api + web + postgres + workers (env shared via YAML anchor)
Makefile      Unix-style; on Windows run the underlying commands directly
```

**Dev environment quirks:** Windows host; Python venv at `apps/api/.venv`
(`.venv/Scripts/python`); `make` targets are Unix-style. Backend: `uvicorn src.main:app --reload`
from `apps/api`. Frontend: `npm run dev` from `apps/web`. No seeding needed — the UI builds
workspaces from tickers; `POST /api/examples/private-deal` loads a fictional private-company demo
("Meridian Compliance Software") through the real governed pipeline.

## 4. Domain map (what each subsystem does)

- **Ingestion & derivation** — `edgar_client` (all network I/O, cached via blob store),
  `sec_ingestion_service`, `sec_financials` (XBRL → financial summary, trends, forensic inputs,
  quarterly/TTM with Q4 = FY−(Q1..Q3) derivation, segments, debt maturities), `filing_sections`
  (10-K section/paragraph chunking). **Critical convention:** annual duration facts are keyed by
  their `CY####` frame — the `fy` field in live Company Facts is the *reporting filing's* fiscal
  year (every comparative in one 10-K shares it) and must never be a period key.
- **Analysis pipeline** — `analysis_service.run_full_analysis`: atomically regenerates risks /
  plan / questions / IC memo / red-team from evidence, then seals an `AnalysisRun` +
  `ArtifactVersion` in the same transaction. Runs as a durable background job with heartbeats.
- **Underwriting engine** — `underwriting_model_service` (~2,100 lines, the quant core): LBO
  projection (debt schedules, revolver, PIK, cash sweep; **ending cash may go negative and
  carries forward**), covenants (FCCR uses *scheduled* amortization), DCF, sensitivity, reverse
  stress, Monte Carlo (wipeouts are −100 % IRR total losses, never censored), returns
  attribution (exactly reconciled), covenant headroom, AST-safe driver formulas (whitelisted
  nodes, finite literals only), working-capital peg/seasonality, recap/bolt-on events, exit
  readiness, football field, fund construction. Append-only case versions with CAS.
- **Data import & QoE** — `underwriting_data_service`: CSV/XLSX import (formula-rejecting,
  archive-safe), account mapping, reconciliation, sealed source snapshots, QoE bridge
  (reported → management → sponsor → covenant EBITDA), four-eyes QoE adjustment decisions.
- **Deal workflow & governance** — `deal_workflow_service`: Org → Fund → Deal, stage gates,
  workstreams/tasks/milestones, diligence requests, decision ledger, IC packets (server-assembled
  from approved sources, frozen with content hash), four-eyes decisions, conditions-to-close,
  append-only audit outbox that fans out to webhooks and notifications.
- **Deal intelligence** — `deal_intelligence_service`: immutable versioned data-room documents,
  chunking with exact locators, cited/abstaining Q&A, structured claim extraction with human
  review (approve/reject/edit as new revisions), document comparison, claim promotion to
  Evidence. `cross_corpus_qa_service` answers over public filings + confidential data room with
  per-citation provenance labels (ties prefer the public corpus).
- **Retrieval & search** — `retrieval_service` (BM25 + optional keyless feature-hashing embeddings
  with RRF hybrid), workspace FTS (FTS5/tsvector), retrieval eval harness gating recall@k/MRR in CI.
- **Research extensions** — forensics (Altman/Piotroski/Beneish/QoE flags), valuation/WACC,
  8-K events, insider patterns, ownership (13F/13D-G), proxy/DEF 14A compensation, govcon
  (USAspending), macro (FRED), news (GDELT), watchlists, signals overview, filing diff.
- **Identity & security** — see §5.
- **Platform** — durable job queue (`job_service` + worker, stale-job recovery), SSE, Prometheus
  `/metrics` (route-template labels), request ids, per-org quotas, blob storage abstraction
  (local disk / S3-compatible), notifications (audit-outbox projection; directed @mention rows
  are recipient-private), share links (capability tokens), export bundle (offline-verifiable ZIP),
  memo redlines, IC meeting mode, review inbox (four planes), audit explorer, onboarding tour.

## 5. Security model (worth internalizing before touching routes)

Authentication paths, resolved in `main.py` middleware:

- **`dls_` sessions** — PBKDF2 password auth, revocable opaque tokens, lockout, rate-limited
  auth endpoints. Browser flow bridges the token into an HttpOnly cookie and calls through a
  same-origin `/backend` proxy so the token never lives in JS.
- **`dlk_` API keys** — scoped, **deny-by-default**: `api_key_service.api_key_scope_for(method,
  path)` is a central route→scope policy enforced in middleware on *every* request; uncovered
  routes 403 for keys.
- **Trusted-service internal token** — automation path where actor headers are honored *after*
  proving possession of `INTERNAL_API_TOKEN`. These principals are flagged
  (`is_service_account` → `ActorContext.via_trusted_service`) and **rejected as reviewers on all
  four-eyes planes** (claim review, diligence review, blocking IC comments, QoE decisions),
  because their actor id is caller-chosen.
- **OIDC SSO** — opt-in (`OIDC_ENABLED`), off by default. Enforces single-use TTL-bounded
  `state`, `nonce` echo, and required `iss`/`aud`/`exp`. **Documented caveat: the id_token
  signature is NOT verified** — production would need JWKS verification.
- **Tenant guard** — workspace-path middleware plus service-level org checks; body-addressed
  routes must scope themselves via `common.get_workspace_scoped_or_404`. Cross-tenant = 404.
- **Coarse + fine authorization** — viewer role is read-only (middleware backstop); G49
  capability matrix (`require_capability`) gates approve/decide endpoints.
- **Rate limiting / quotas** — in-process limiters (auth paths incl. OIDC GETs, demo build
  throttle, per-org quota buckets). All are single-process by design; multi-replica deployments
  need a shared limiter (documented in code).

## 6. Testing, CI, and quality history

- **Backend:** `pytest` — 69 files / ~550 tests, all offline (live-SEC alignment tests
  auto-skip; conftest forces `LLM_MODE=mock`, `AUTH_REQUIRED=false`). Style: regression tests
  reproduce the bug they guard, with docstrings explaining the failure mode.
  `test_phase0_truth.py` guards data-truth gates; `test_no_uncited_material_claims.py` is the
  faithfulness guard; `test_migrations.py` guards Alembic integrity incl. Postgres's 63-char
  identifier cap.
- **Frontend:** vitest, 54 tests colocated with components; `tsc --noEmit` and eslint clean.
- **CI (`.github/workflows/ci.yml`):** api ruff + alembic + pytest (SQLite **and Postgres
  matrix**), web vitest/lint/build, compose smoke test, retrieval-eval quality gate.
- **Quality history:** three adversarial audit waves have been run and fully remediated —
  2026-07-15 (5 HIGH/8 MED), 2026-07-16 (7 HIGH/14 MED, commit `2498feb`), 2026-07-17
  (2 HIGH/9 MED/9 LOW, commit `2c65f20`). Every finding landed with a regression test. The
  commit messages of `2498feb` and `2c65f20` are the best summaries of the sharp edges that were
  found and fixed (period keying, wipeout censoring, negative-cash carry, scope enforcement,
  four-eyes bypass, existence oracles, outage-as-empty rendering).

## 7. Current state and open items

**State:** Waves 1–4 complete (`FEATURE_LEDGER.md` 65/65, `ROADMAP-WAVE4.md` 50/50). CI green.
Working tree clean at `2c65f20`.

Open items, roughly in priority order:

1. **Push the two unpushed commits** (`2498feb`, `2c65f20`) once the owner is ready — they
   contain all 2026-07-16/17 audit fixes.
2. **Hosted public demo** — `docs/deploy-demo.md` is the runbook (VPS/domain/TLS, `DEMO_MODE=true`,
   EDGAR cache TTL, demo-cleanup worker). Deploy has not happened yet; repo pinning on the GitHub
   profile is also pending.
3. **Documented security scope-cuts** (fine as portfolio caveats, must not silently regress):
   OIDC signature verification; in-process rate limiters/OIDC state (single-process assumption);
   share-link tokens as capabilities (deliberately outside API-key scope catalog).
4. **Minor stale docs:** `apps/api/README.md` and the Makefile seed help still reference the
   deleted "ChainAssure" synthetic demo (pre-2026-07-08 pivot).
5. `alembic check` needs a migrated DB locally; CI covers migration integrity.

## 8. Where to look first (fast index)

| Question | File |
| --- | --- |
| What endpoints exist and their contracts | `docs/CONTRACTS.md` (route table; ~220 routes) |
| How a workspace builds from a ticker | `src/services/sec_ingestion_service.py`, `analysis_service.py`, `job_service.py` |
| XBRL → numbers (and the never-impute rules) | `src/services/sec_financials.py`, `edgar_client.py` |
| The LBO/quant engine | `src/services/underwriting_model_service.py` |
| Governance / four-eyes / IC | `src/services/deal_workflow_service.py`, `deal_intelligence_service.py` |
| Auth, middleware, rate limits, quotas | `src/main.py`, `src/services/api_key_service.py`, `identity_service.py`, `oidc_service.py` |
| Shared service helpers (404s, versioned inserts) | `src/services/common.py` |
| Frontend API client + types | `apps/web/src/lib/api.ts`, `types.ts`, `serverApi.ts` |
| Design system | `apps/web/DESIGN.md` |
| Why a past decision was made | `git log` — commit messages are deliberately explanatory |
