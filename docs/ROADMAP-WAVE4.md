# Wave 4 Roadmap — 50 planned capabilities

Planning ledger for the next development wave. Same rules as `FEATURE_LEDGER.md`: an item is
`done` only when its implementation **and** the named acceptance evidence exist in the worktree.
Nothing here is started; this document is the groomed backlog, sequenced by portfolio value.

**Design constraints carried forward from Waves 1–3** (non-negotiable):
keyless-by-default data sources; deterministic outputs unless a human explicitly enables the LLM;
never impute missing data; explicit source status instead of false-clean empties; append-only
governed records; every material claim cites resolvable evidence.

Carryovers from the Wave 3 ledger: F41 → G17, F55 → G18.

---

## Theme A — Retrieval & grounded AI (10)

The deepest differentiator for AI-role interviews: real hybrid retrieval, measured with evals,
and generation that is provably constrained by evidence.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G01 | Hybrid retrieval: pgvector embeddings fused with BM25 via reciprocal-rank fusion; local ONNX embedding model so the default stays keyless | L | fusion-ranking unit tests + identical-API contract test vs BM25-only |
| G02 | Embedding ingestion pipeline: chunk embeddings persisted at ingest (`DocumentChunk.embedding_id` wired), plus a backfill command for existing workspaces | M | ingest + backfill idempotency tests |
| G03 | Retrieval evaluation harness: golden question set over fixture filings; recall@k and MRR computed in CI and tracked in a committed metrics file | M | CI job fails on metric regression below floor |
| G04 | Grounded synthesis mode (live LLM): fluent answers composed **only** from retrieved extracts, gated by the citation auditor, abstention preserved | L | adversarial tests: fabricated number/citation → answer rejected, extractive fallback served |
| G05 | LLM-as-judge faithfulness evals with persisted eval runs and a quality dashboard per model/prompt version | L | judged-run schema tests + dashboard rendering test |
| G06 | Abstention calibration: score distributions for answered vs abstained questions; thresholds justified by a committed calibration study | M | calibration notebook/doc + threshold boundary tests |
| G07 | Cross-year 10-K semantic diff: risk-factor drift (added / removed / materially changed) with citations into both filings | L | drift classification tests on fixture year-pairs |
| G08 | Unified cross-corpus Q&A: one question over filings + data-room docs with provenance-aware citations (public vs confidential clearly labeled) | M | mixed-corpus citation labeling tests |
| G09 | Embedding-similarity comp discovery from business descriptions, shown side-by-side with the SIC-code method and its disagreements | M | similarity-ranking tests + UI comparison test |
| G10 | Prompt & model-config registry: versioned, hashed prompt manifests bound to every LLM-touched artifact run (reproducible LLM ops) | M | manifest hash round-trip + tamper-detection tests |

## Theme B — Public-data research depth (10)

Widens the moat of "real data, no keys" — the research-analyst credibility layer.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G11 | 10-Q quarterly ingestion + trailing-twelve-month metric derivation | M | TTM arithmetic tests across fiscal-year boundaries |
| G12 | XBRL segment-level revenue (dimensional facts) with segment trend charts | L | dimensional-fact extraction tests on real filers |
| G13 | DEF 14A proxy ingestion: executive compensation table + governance red flags (staggered board, dual-class) | L | comp-table parse tests + flag rule tests |
| G14 | 13F institutional ownership snapshot + holder-concentration analysis | M | parse + concentration math tests |
| G15 | 13D/13G activist-stake detection wired into the signals timeline | S | event classification tests |
| G16 | Debt maturity schedule extraction from filings + maturity-wall chart | L | schedule extraction tests + never-impute gaps test |
| G17 | Fiscal-period consistency diagnostics (carryover F41): mixed-period operands flagged, never silently blended | M | mismatch detection tests |
| G18 | Consolidated signals overview page (carryover F55): one screen aggregating events/insiders/news/themes with per-source status | S | page/API rendering test |
| G19 | Watchlists with scheduled refresh: track N companies, detect new filings, emit notification/webhook events through the existing outbox | M | scheduler + dedup + outbox event tests |
| G20 | Insider-pattern analytics: clustered buying/selling windows, 10b5-1 plan flags, officer-vs-director splits | M | clustering + classification tests |

## Theme C — Underwriting & quantitative depth (10)

The PE-domain fluency layer — complex, and exactly what a diligence/valuation interviewer probes.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G21 | Monte Carlo LBO: driver distributions, percentile IRR/MoIC bands, deterministic seeding so runs are reproducible | L | seeded-distribution tests + percentile assertions |
| G22 | Returns attribution bridge: entry/exit multiple vs deleveraging vs EBITDA growth decomposition, reconciling exactly to total return | M | attribution-sums-to-total test |
| G23 | Covenant headroom projection: quarter-by-quarter headroom under each case with breach-quarter detection | M | breach boundary tests |
| G24 | Driver-based operating model: user-defined drivers with formula validation, cycle detection, and provenance on every derived line | L | formula parser + cycle rejection tests |
| G25 | Working-capital seasonality modeling from monthly imports (peg by month, not annual average) | M | seasonal peg tests on fixture monthlies |
| G26 | Dividend recap and bolt-on acquisition modeling inside case versions | L | sources/uses + returns integration tests |
| G27 | Management-vs-sponsor case variance analysis: line-level deltas with materiality ranking | S | variance math tests |
| G28 | Exit readiness scorecard + hold-period sensitivity (3/5/7-year grids) | M | scorecard component tests |
| G29 | Fund-level portfolio construction: aggregated exposure vs concentration limits, simple pacing model | L | limit-breach detection tests |
| G30 | Valuation football field: triangulation methods on one chart with explicit method weights and excluded-method reasons | S | chart data-contract test |

## Theme D — Platform engineering & scale (10)

The senior-engineer credibility layer: the app already works; this makes it *operable*.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G31 | Durable job queue: workspace builds move from in-process BackgroundTasks to a DB-backed job table + worker with retries, heartbeats, and stale-claim recovery (generalizes the webhook outbox pattern) | L | crash-recovery + at-least-once tests |
| G32 | Server-sent events for live build progress and notifications (polling kept as fallback) | M | SSE stream integration test |
| G33 | In-app notification center fed by the existing audit outbox | M | event-to-notification mapping tests |
| G34 | Full-text search across all workspace artifacts (SQLite FTS5 / Postgres tsvector behind one interface) | L | parity tests on both engines |
| G35 | Observability: Prometheus `/metrics`, structured JSON logs, request-ID propagation end-to-end (web proxy → API → workers) | M | metrics endpoint + request-ID round-trip tests |
| G36 | Postgres CI matrix: the full backend suite runs against a Postgres service container in addition to SQLite | M | green matrix required for merge |
| G37 | Load-test harness (k6/Locust) with budgeted p95 latencies on the hot endpoints; CI perf smoke | M | perf budget file + smoke job |
| G38 | Scoped API keys for programmatic access + generated OpenAPI client | M | scope enforcement matrix tests |
| G39 | Per-organization quotas and rate limits (generalizes the demo limiter into tenant policy) | M | quota boundary tests |
| G40 | Blob-storage abstraction: local disk default, S3-compatible option for data-room docs and the EDGAR cache | L | backend-parity contract tests |

## Theme E — Collaboration & governance UX (10)

Rounds the product into something a team could actually run a deal in.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G41 | Comment threads with @mentions on any governed artifact (risk, adjustment, memo section, packet) | L | thread/permission tests + mention notification test |
| G42 | "My reviews" inbox: one queue spanning QoE decisions, claim reviews, diligence responses, and IC comments awaiting the signed-in actor | M | cross-plane queue aggregation tests |
| G43 | Audit-log explorer UI: filter by actor/entity/date, export CSV | S | filter + export tests |
| G44 | Read-only tokenized share links for a frozen workspace snapshot (revocable, expiring) — lets an interviewer walk a finished deal with zero setup | M | token scope/expiry/revocation tests |
| G45 | Workspace export bundle: IC memo PDF + evidence appendix + hash manifest, verifiable offline against the packet verifier | M | bundle round-trip verification test |
| G46 | IC meeting mode: full-screen packet presentation with inline decision capture and condition logging | M | presentation-flow component tests |
| G47 | Memo redlines: side-by-side diff of any two analysis runs with changed-claim highlighting | M | diff correctness tests |
| G48 | Optional OIDC SSO with role mapping (config-gated; password auth remains the default) | L | OIDC callback + role mapping tests |
| G49 | Fine-grained permission matrix beyond the four roles (per-capability grants, deny-by-default) | L | exhaustive permission table tests |
| G50 | Guided onboarding tour + contextual empty states across all workbenches | S | tour state-machine component tests |

---

## Sequencing

Four sub-waves, ordered by interview value per unit of effort:

1. **Wave 4a — "Measured AI"** (G01–G05, G10, plus G31/G32 as enablers): hybrid retrieval with a
   CI-gated eval harness and auditably grounded generation. This is the strongest talking track
   for AI roles: *"I didn't just add RAG — I measured it, gated CI on it, and made generation
   provably faithful."*
2. **Wave 4b — "Analyst depth"** (G11–G20, G07): quarterly/segment/proxy/ownership data and the
   10-K drift diff. Widens the real-data moat and produces demo moments interviewers recognize.
3. **Wave 4c — "Institutional model"** (G21–G30): Monte Carlo, attribution, covenant headroom,
   driver models. The PE-domain fluency layer.
4. **Wave 4d — "Operable platform"** (G33–G40, G41–G50 as capacity allows): observability,
   Postgres matrix, perf budgets, collaboration. The senior-engineering maturity layer.

**Top 10 if only 10 get built:** G01, G03, G04, G07, G21, G22, G31, G32, G36, G44.
(G44 — shareable read-only deal links — is the single highest-leverage feature for interviews:
it turns every conversation into "here, click this link.")

## Explicit non-goals

Market prices, trading data, and paid feeds (no free source; would break keyless-by-default);
order execution or anything advice-like; multi-region HA (a portfolio demo does not need it and
claiming it would be theater).
