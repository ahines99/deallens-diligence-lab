# Wave 3A/3B — Institutional Underwriting Workbench

Wave 3 is a full-stack extension of DealLens from public-company issue spotting into private-equity
underwriting, deal execution, evidence review, and investment-committee governance. Calculations are
deterministic. Imported values, model versions, claims, reviews, and IC decisions retain hashes and
source/version metadata.

## Core workflow

```text
Organization / Fund / Deal
          |
          +-- Data room and management financial imports
          |       +-- account mapping, reconciliation, exceptions
          |       +-- exact page / sheet / cell provenance
          |
          +-- QoE adjustment review
          |       reported -> management -> sponsor -> covenant EBITDA
          |
          +-- Base / upside / downside underwriting cases
          |       operating model -> debt/covenants -> returns/DCF/stress
          |
          +-- Workstreams, tasks, requests, thesis/issues/decisions
          |
          +-- Approved claims and cited Q&A
          |
          `-- Frozen IC packet -> review -> decision -> controlled export
```

## API groups

### Private financials and QoE

- `/api/workspaces/{workspace_id}/underwriting/private-target`
- `/api/workspaces/{workspace_id}/underwriting/sources`
- `/api/workspaces/{workspace_id}/underwriting/account-mappings`
- `/api/workspaces/{workspace_id}/underwriting/financial-imports`
- `/api/workspaces/{workspace_id}/underwriting/financial-imports/csv`
- `/api/workspaces/{workspace_id}/underwriting/financial-imports/xlsx`
- `/api/workspaces/{workspace_id}/underwriting/financial-facts`
- `/api/workspaces/{workspace_id}/underwriting/import-exceptions`
- `/api/workspaces/{workspace_id}/underwriting/reconciliations`
- `/api/workspaces/{workspace_id}/underwriting/qoe-adjustments`
- `/api/workspaces/{workspace_id}/underwriting/qoe-bridge`
- `/api/workspaces/{workspace_id}/underwriting/analysis-runs`
- `/api/workspaces/{workspace_id}/underwriting/artifact-versions`

The XLSX importer reads the first visible worksheet. Required headers are `raw_account`, `statement`,
`period_end`, `period_type`, and `value`. Optional headers are `canonical_account`, `period_start`,
`scale`, `unit`, and `currency`. Formula/error cells, active content, unsafe archives, ambiguous headers,
and oversize workbooks are rejected. Source locators retain the exact worksheet and cell.

### Underwriting and returns

- `/api/workspaces/{workspace_id}/underwriting/calculate`
- `/api/workspaces/{workspace_id}/underwriting/cases`
- `/api/workspaces/{workspace_id}/underwriting/case-set`
- `/api/workspaces/{workspace_id}/underwriting/working-capital-peg`
- `/api/workspaces/{workspace_id}/underwriting/valuation-triangulation`
- `/api/workspaces/{workspace_id}/underwriting/sensitivity`
- `/api/workspaces/{workspace_id}/underwriting/reverse-stress`

Cases are append-only and carry input/output SHA-256 hashes. The standard projection is monthly for the
first 24 months and annual for years 3–5. Debt mechanics support revolver, term loan, second lien,
mezzanine, and seller-note tranches with floors, spreads, amortization, PIK, maturity, cash sweep, OID,
and fees. The engine reports liquidity shortfalls, covenant headroom/first breach, unpaid maturities,
dated XIRR, MOIC, and FCFF DCF.

### Deal execution and IC

- `/api/organizations`, `/api/organizations/{id}/funds`, `/api/funds/{id}/deals`
- `/api/deals/{id}/gates`, `/team`, `/workstreams`, `/milestones`, `/tasks`
- `/api/deals/{id}/diligence-requests`, `/ledger`, `/ic-packets`, `/conditions`, `/audit-events`
- `/api/ic-packets/{id}/readiness`, `/submit`, `/comments`, `/decisions`, `/exports`

IC submission freezes the packet content hash. Four-eyes protection can reject self-approval, blocking
comments must be resolved, and conditional approvals create tracked conditions to close. Export files
embed packet/version/content-hash metadata and persist their own SHA-256 manifests.

### Document intelligence

- `/api/deals/{deal_id}/intelligence/documents` and `/documents/upload`
- `/api/deals/{deal_id}/intelligence/qa`
- `/api/deals/{deal_id}/intelligence/extractions` and `/claims`
- `/api/intelligence/claims/{claim_id}/review`
- `/api/deals/{deal_id}/intelligence/comparisons`
- `/api/workspaces/{workspace_id}/intelligence/sec-comparisons`
- `/api/deals/{deal_id}/intelligence/evaluations`

Q&A is deliberately extractive and abstains when retrieval cannot support an answer. Numeric claims and
structured fields retain exact locators. Extracted claims stay pending until a reviewer approves,
rejects, or edits them; review events and claim revisions are append-only.

### Versioned API and signed integrations

Every `/api/...` contract is also available under the stable `/api/v1/...` prefix. Versioned responses
include `X-DealLens-API-Version: 1`; the unversioned routes remain available for existing clients.

- `/api/organizations/{organization_id}/webhooks`
- `/api/webhooks/{endpoint_id}` and `/test`
- `/api/organizations/{organization_id}/webhook-deliveries`
- `/api/webhook-deliveries/{delivery_id}/send`
- `/api/organizations/{organization_id}/webhook-deliveries/process`

Workflow audit events enter a transactional outbox in the same commit as the business mutation. Endpoint
secrets are Fernet-encrypted at rest. Each canonical JSON body is signed as
`HMAC-SHA256(timestamp + "." + body)` and sent with event, delivery, timestamp, and signature headers.
Delivery IDs are stable for consumer deduplication; failures use exponential retry and end in a visible
dead-letter state. HTTPS and public destinations are required unless local-development HTTP is explicitly
enabled. The Docker stack runs the durable delivery worker as a separate service.

## Identity and tenant boundary

The API authenticates expiring opaque bearer sessions whose hashes are stored server-side and can be
revoked. With `AUTH_REQUIRED=true` (the default), the verified session principal supplies actor,
organization, and role context; spoofed actor or organization headers cannot override it. Development
actor headers are accepted only when authentication is explicitly disabled for an isolated local demo.
Deal-linked workspace routes return 404 across organization boundaries, and viewer memberships are
read-only.

Set `WEBHOOK_ENCRYPTION_KEY` to a Fernet key before registering integrations. Keep
`WEBHOOK_ALLOW_INSECURE_HTTP=false` outside isolated local development. When `AUTH_REQUIRED=true`,
webhook administration additionally requires an `integration_admin` or `organization_admin` role claim.

## Database migrations

Docker applies migrations automatically. For local or manual deployments:

```bash
cd apps/api
python -m alembic -c alembic.ini upgrade head
```

The initial migration supports both an empty database and a legacy DealLens database previously created
with SQLAlchemy `create_all`; existing tables are preserved and missing Wave 3 tables are added.

## Verification

```bash
cd apps/api
python -m pytest
python -m ruff check src tests migrations

cd ../web
node_modules/.bin/tsc --noEmit
npm.cmd run build          # Windows PowerShell
```

Scanned image-only PDFs currently require future OCR. Binary data-room content is stored in the database
for this local portfolio implementation; a production deployment should use encrypted object storage,
malware scanning, retention policy, and legal-hold controls.
