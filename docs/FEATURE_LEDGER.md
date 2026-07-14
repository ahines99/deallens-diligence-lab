# Portfolio Expansion Acceptance Ledger

This ledger is the authoritative completion checklist for the post-Wave-3 audit program. A feature is
`done` only when its implementation and verification evidence are both present in the current worktree.
Bug fixes from the audit are tracked separately so the requirement for at least 50 **new** capabilities
cannot be satisfied by relabeling repairs as features.

## Audit defect gates

| ID | Defect gate | Status | Required evidence |
|---|---|---|---|
| B01 | Refresh uses `accession_number` and is idempotent | done | focused refresh test plus repeated ingest |
| B02 | Non-December fiscal-year XBRL operands share a reporting period | done | AAPL/MSFT/CRWD alignment tests |
| B03 | Legacy valuation uses correctly labelled FCFF/FCFE concepts | done | deterministic valuation tests |
| B04 | Legacy LBO disclosures match actual debt-paydown mechanics | done | API/UI assertion |
| B05 | Client input cannot masquerade as SEC/XBRL provenance | done | schema and service rejection tests |
| B06 | Missing forensic inputs remain unscored/unknown | done | incomplete-input tests |
| B07 | FRED year-over-year comparisons are date/frequency aware | done | daily/monthly/quarterly tests |
| B08 | USAspending totals are complete and pagination-aware | done | paginated response tests |
| B09 | Form 4 transactions use transaction codes, not only A/D | done | grant/buy/sell/withholding tests |
| B10 | SQLite foreign keys are enforced | done | orphan/cascade test |
| B11 | Evidence references are unique and concurrency safe | done | uniqueness/race test |
| B12 | Regeneration preserves governed historical artifacts | done | multi-run history test |
| B13 | LLM-polished output is rejected on citation/numeric drift | done | adversarial provider tests |
| B14 | Stale calculations are never presented as current | done | frontend state tests |
| B15 | Docker frontend build and SSR service routing work | done | production build, standalone SSR/BFF smoke, and Compose config parse; local Docker engine unavailable |

## New capabilities (minimum required: 50)

| ID | Capability | Status | Acceptance evidence |
|---|---|---|---|
| F01 | Password-based user registration with strong hashing | done | auth API tests |
| F02 | Password login with expiring opaque sessions | done | auth API tests |
| F03 | Server-side revocable session records | done | logout/revocation tests |
| F04 | Current-principal (`/auth/me`) endpoint | done | scoped response test |
| F05 | Explicit logout endpoint | done | revoked-token test |
| F06 | Organization membership model | done | migration + membership tests |
| F07 | Owner/admin/member/viewer role enforcement | done | role matrix tests |
| F08 | Verified organization switching | done | membership-bound switch test |
| F09 | Workspace-to-organization ownership | done | migration + ORM test |
| F10 | Tenant-filtered workspace discovery | done | cross-tenant list test |
| F11 | Tenant-scoped confidential document downloads | done | cross-tenant download test |
| F12 | Per-workspace external-LLM consent control | done | policy tests and UI control |
| F13 | Portfolio command-center page | done | rendered page + API test |
| F14 | Portfolio headline KPI strip | done | aggregation tests |
| F15 | Pipeline stage-funnel analytics | done | stage aggregation test |
| F16 | Deal search across code/name/target | done | query tests and UI control |
| F17 | Pipeline stage filter | done | API/UI filter test |
| F18 | Pipeline fund filter | done | API/UI filter test |
| F19 | Sector exposure analytics | done | target-sector aggregation test |
| F20 | Strategy exposure analytics | done | fund-strategy aggregation test |
| F21 | Upcoming IC calendar | done | date-window tests |
| F22 | Overdue-task review queue | done | overdue boundary tests |
| F23 | Workstream-health summary | done | blocked/late aggregation test |
| F24 | Diligence-request SLA dashboard | done | aging/status tests |
| F25 | Cross-deal critical-risk register | done | severity/status tests |
| F26 | Conditions-to-close tracker | done | open/due aggregation test |
| F27 | Team workload by assignee | done | assignment aggregation test |
| F28 | Deal stage-aging analytics | planned | transition-time calculation test |
| F29 | Explainable deal-readiness score | planned | component scoring tests |
| F30 | Latest base/upside/downside returns snapshot | planned | version-selection tests |
| F31 | Downside-protection watchlist | done | liquidity/return threshold tests |
| F32 | Covenant-breach watchlist | done | breach extraction tests |
| F33 | Portfolio dashboard CSV export | done | content/schema test |
| F34 | Source-health status panel | planned | ready/partial/failed aggregation test |
| F35 | Source-freshness and as-of aging | planned | freshness boundary tests |
| F36 | Account-mapping coverage score | done | mapped/unmapped test |
| F37 | Reconciliation-health score | done | passed/incomplete/failed test |
| F38 | Import-exception aging queue | done | open-age sorting test |
| F39 | QoE adjustment materiality analysis | done | percentage/band tests |
| F40 | Reported-to-sponsor EBITDA variance | done | bridge aggregation test |
| F41 | Fiscal-period consistency diagnostics | planned | mismatch detection tests |
| F42 | Server-owned provenance labels | done | user/XBRL source tests |
| F43 | Financial-import dry-run preview | done | no-write preview test |
| F44 | Approved intelligence claims promoted to governed evidence | done | claim approval bridge test |
| F45 | Exact claim-to-document source bindings in IC materials | done | packet binding test |
| F46 | Server-assembled IC packets from model-of-record IDs | done | tamper-rejection test |
| F47 | Immutable IC case-version bindings | done | upstream-change verification test |
| F48 | Approved-claim-only IC inclusion policy | done | pending/rejected exclusion test |
| F49 | IC packet verification endpoint | done | `GET /api/ic-exports/{id}/verification` valid/tampered test (`test_export_verification_endpoint_reports_valid_and_rehashed_tampering`) |
| F50 | Unified cross-plane activity timeline | done | ordered event aggregation test |
| F51 | Runtime memo faithfulness report | planned | citation/numeric diagnostics test |
| F52 | Local actor/role switcher for governance demos | done | frontend interaction test |
| F53 | Demonstrable second-actor four-eyes workflow | done | submitter/approver UI test |
| F54 | Discoverable Signals navigation group | done | navigation/build test |
| F55 | Consolidated signals overview | planned | page/API rendering test |
| F56 | Expandable verbatim evidence excerpts | done | component test |
| F57 | Model-input dirty-state indicator | done | frontend state test |
| F58 | Stress-analysis stale-state indicator | done | frontend state test |
| F59 | Decision-grade deal overview | planned | rendered KPI/risk/source test |
| F60 | Production Next.js standalone container | done | `apps/web/Dockerfile` (standalone output) built by the CI compose job; see B15 evidence |
| F61 | GitHub CI quality pipeline | done | `.github/workflows/ci.yml` (api ruff/alembic/pytest, web audit/test/lint/typecheck/build, compose smoke) mirroring local commands |
| F62 | Webhook dead-letter replay | done | replay state-machine test |
| F63 | Webhook delivery-health metrics | done | aggregation endpoint test |
| F64 | IC export manifest verifier | done | valid/tampered export tests |
| F65 | System/source health dashboard | done | health aggregation/API test |

## Completion rule

Completion requires at least 50 `F##` rows marked `done`, every B01-B15 gate either fixed or explicitly
removed from the shipped product, all tests/builds/migrations green, and a final requirement-by-requirement
audit against the current worktree. Planned or partially implemented rows do not count.

Current verified tally: **15/15 defect gates closed and 56/65 capabilities done**. F02 intentionally
uses expiring, server-stored, hashed, revocable opaque tokens instead of self-contained signed tokens.
The remaining 9 ideas (F28–F30, F34–F35, F41, F51, F55, and F59) stay planned and are not included in
the completed count. Note: the portfolio dashboard already computes stage-age days, readiness component
scores, returns snapshots, and per-deal source health (`portfolio_service.py`), so F28–F30 and F34–F35
are partially implemented — they remain planned until their dedicated boundary/acceptance tests exist.
