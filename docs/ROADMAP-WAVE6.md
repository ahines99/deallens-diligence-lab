# Wave 6 Roadmap — 25 capabilities

**Status: 5 / 25 complete (Theme M done: G79-G83).** Same rules as `FEATURE_LEDGER.md` and prior roadmaps: an item is
`done` only when its implementation **and** its acceptance test/artifact both exist in the
worktree, verified green (backend pytest + frontend vitest + ruff + tsc + lint).

**Where Wave 6 points.** Wave 5 made the LLM the generator and the deterministic layer the
verifier. Wave 6 deepens that in the three directions a PE-technical reviewer will probe next:
**agentic workflows that stay inside governance** (the agent can propose, draft, and stream —
but never approve), **peer/market context from the same keyless public data**, and **AI
operations that are measured like production** (cost telemetry, prompt A/B, eval-gated model
changes). Plus the underwriting depth and trust/collaboration items that keep the domain story
credible.

**Constraints carried forward (non-negotiable):** keyless-by-default data; deterministic unless
a human enables the LLM; never impute; explicit source status over false-clean empties;
append-only governed records; every material claim cites resolvable evidence; CI hermetic
(`LLM_MODE=mock` never touches a network; new ML deps only as optional extras); the four-eyes
boundary holds for automation — agents and services may PROPOSE, only humans APPROVE; in-process
limiter/state assumptions remain single-API-process.

---

## Theme I — Agentic depth (5)

The G57 substrate (governed tools, grounding gate, sealed transcripts) becomes a set of
workflows an analyst would actually run.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G59 | Agent-drafted IC memo sections: the agent drafts one memo section at a time from governed tool results; EVERY section passes the grounding gate independently (a rejected section is withheld, others survive); the analyst accepts/rejects per section in the UI and only accepted sections enter the draft memo — human-in-the-loop by construction | L | planned — per-section grounding isolation, rejected-section withholding, accept/reject round-trip, sealed section transcripts |
| G60 | Agent proposals into the four-eyes queues: the agent may PROPOSE QoE adjustments and structured claims (flagged as agent-proposed with the run's manifest bound); proposals land in the existing review inbox; approval remains human-only — an agent-proposed item can never be agent-approved (extends the trusted-service reviewer ban) | M | planned — proposal minting with provenance, inbox surfacing, agent/automation rejected as approver, human approval path unchanged |
| G61 | Streaming agent runs over the existing SSE substrate: the console renders the step timeline live (tool call → result → next round) and survives reconnect mid-run; the sealed artifact remains the source of truth after completion | M | planned — SSE event contract per step, reconnect replay from the transcript, final state parity with the sealed artifact |
| G62 | Agent evaluation harness: a committed golden set of objectives with expected tool-selection patterns and grounding outcomes; measures tool-choice sanity, grounding pass rate, and abstention correctness with scripted providers; CI-gated and surfaced as a `/quality` section | L | planned — committed golden objectives, metrics computation, CI floor, quality-dashboard section with honest status |
| G63 | Comparative agent runs: one objective across the target workspace plus selected comp workspaces; each tool call remains harness-scoped to its own workspace; the merged answer carries per-workspace provenance and the grounding gate runs against the union of that run's tool results only | L | planned — per-workspace scoping proof, merged provenance labeling, cross-workspace grounding isolation |

## Theme J — Peer & market context (5)

Same keyless public data, one level up: how the target sits in its universe.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G64 | XBRL frames peer benchmarking: pull the SEC frames API for the target's SIC peers and report percentile ranks for growth/margins/leverage with explicit coverage counts (peers reporting the concept) — thin frames degrade to "insufficient peer coverage", never a fabricated percentile | L | planned — percentile math on fixture frames, coverage honesty, live-skip test, endpoint + workspace tab |
| G65 | Sum-of-the-parts valuation from G12 segments: per-segment multiples (user-supplied or benchmarked) over segment revenue/EBITDA, reconciled exactly to the consolidated total with an explicit unallocated/eliminations residual — never force-balanced | M | planned — reconciliation identity, residual honesty, consolidated-only workspaces report unavailable |
| G66 | Buyback & dilution analysis: shares-outstanding trend, SBC expense vs repurchases, net dilution rate per fiscal year from XBRL with citations; missing concepts degrade per-year, never interpolated | M | planned — per-year derivation on fixtures, missing-concept honesty, citation binding |
| G67 | Litigation & proceedings: Item 3 extraction from 10-Ks plus the 8-K legal-events timeline, chunked with citations and surfaced as a risk-flag source (degraded-source discipline like the other extensions) | M | planned — section extraction fixtures, risk-flag integration, unavailable-not-clean on outage |
| G68 | Macro-linked Monte Carlo presets: documented mappings from FRED series (rates, spreads, GDP) to driver distributions the user can load and edit before a run; the mapping is a transparent, versioned config — never a hidden model | M | planned — preset generation from fixture FRED data, user-editability, mapping provenance in the MC result |

## Theme K — Underwriting depth (5)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G69 | One-way sensitivity tornado: rank every driver's ± impact on IRR/MoIC around the base case (deterministic, reusing `_apply_variable`), rendered as a tornado chart in the stress workbench | S | planned — ranking math vs hand-computed fixture, symmetric-range contract, chart render test |
| G70 | Dividend recap solver: maximum distribution at a chosen date subject to leverage / coverage / minimum-liquidity constraints, solved by bisection over the existing engine with the binding constraint named — no solution reports WHY | M | planned — solver convergence vs hand-solved fixture, binding-constraint attribution, infeasible-case honesty |
| G71 | Working-capital facility sizing: peak intra-year revolver need from the G25 seasonality model (monthly working-capital swings vs the annual projection), with the peak month and headroom vs the modeled commitment | M | planned — peak-draw math on seasonal fixtures, headroom sign conventions, no-seasonality-data degrades honestly |
| G72 | Fund-level Monte Carlo: aggregate per-deal simulations with shared macro factor draws so deal outcomes correlate through common factors (documented loadings, user-editable); fund construction integration for portfolio IRR/MoIC bands | XL | planned — shared-draw correlation test (correlated vs independent bands differ predictably), deterministic seeding, fund-construction wiring |
| G73 | Year-by-year value-creation waterfall: the G22 attribution bridge decomposed per hold year (EBITDA growth, multiple, deleveraging, cross term per year), reconciling exactly to the total bridge | M | planned — per-year legs sum to the exact G22 total, Decimal reconciliation, chart contract |

## Theme L — Trust & collaboration (5)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G74 | Signed export attestation: Ed25519 signatures over the export/bundle manifest (key from config, public key served), verifiable fully offline; tampering any byte fails verification — extends the existing hash discipline to cryptographic provenance | M | planned — sign/verify round-trip, tamper detection, offline verifier script in the bundle, key-absent degrades to hash-only honestly |
| G75 | Data-room redaction workflow: span-level redactions proposed on a document version and approved four-eyes; approval mints a NEW immutable redacted version (originals untouched); QA, search, and share-surfaces serve the redacted version to non-privileged viewers | XL | planned — span proposal/approval flow, immutability of originals, redacted-version routing in QA/search, privilege matrix |
| G76 | Share-link analytics + watermarking: per-link view events (when, coarse where), a visible per-link watermark rendered into shared views, and one-click revocation surfaced with the analytics | M | planned — view-event capture, watermark presence in shared render, revocation immediacy |
| G77 | Notification digests: per-user in-app daily/weekly digest rolling up watchlist filings, audit events, and review-inbox aging (no email dependency); directed-notification privacy rules carry over | S | planned — digest assembly windows, recipient privacy, dedup vs live notifications |
| G78 | Review-inbox SLAs: per-plane aging thresholds with an aging report (oldest items, breach counts) and digest/notification hooks when an item crosses its SLA | S | planned — aging math across planes, threshold config, breach surfacing in inbox + digest |

## Theme M — AI ops & retrieval (5)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G79 | Persisted extraction comparison: run G52's `compare_with_scanner` for a workspace on demand, persist the result, and fill the `/quality` `extraction_comparison` placeholder with real overlap/llm-only/scanner-only data over time | S | `done` — run+persist as append-only `extraction_comparison` ArtifactVersion, quality section fed by the newest comparison, mock/no-consent → not_run with nothing persisted, supersession (`apps/api/tests/test_extraction_comparison.py`) |
| G80 | LLM cost telemetry: token usage captured per live call (provider response usage fields) and bound to manifests/sealed runs; per-org spend rollup beside the G58 quota; `/quality` cost section | M | `done` — provider-seam capture on every live call shape, own-session never-raise persistence, org vs untagged attribution via request contextvar, 24h rollup on `/quota-usage` (`apps/api/tests/test_llm_usage.py`) |
| G81 | Prompt A/B evaluation: run two registered versions of a prompt over the committed golden set with the judge; side-by-side faithfulness report; promoting a new prompt version requires the eval artifact — registry-versioned, tamper-evident | L | `done` — registered-vs-candidate judged over the golden set, blob-envelope history (cap 20), winner semantics, quality section, promotion convention documented (`apps/api/tests/test_prompt_ab.py`) |
| G82 | Optional ONNX cross-encoder reranker (extends the G55 extra): rerank the hybrid top-k; default OFF and eval-gated — it ships default-on only if it beats RRF on the committed golden set, and degrades explicitly when the extra/model is absent | L | `done` — default-off contract byte-identical to RRF, fake-reranker reorder + stable tie-break, unavailable-with-note degradation, `eval_gate` (beat hybrid on MRR AND recall@5, committed metrics = promotion artifact) (`apps/api/tests/test_reranker.py`) |
| G83 | pgvector storage on Postgres deployments: the same embedding contract stored in a pgvector column with DB-side cosine; SQLite keeps the Python path; parity tests prove identical rankings across backends; migration + Postgres CI matrix coverage | L | `done` — dialect-gated migration `d5f2b8c3a1e9` (SQLite no-op, extension-absent skip), lazy vector backfill + DB-side cosine mirroring the Python ordering exactly, savepoint fallback on any DB error, CI Postgres image → pgvector/pgvector:pg16, Postgres-only parity tests (`apps/api/tests/test_pgvector_parity.py`) |

**Suggested sequencing:** M-theme first (G79/G80 are small and close visible loops; G81/G82/G83
harden the measurement story) → I-theme (agentic depth is the differentiator; G62's eval harness
should land before G59/G60 ship agent output into governed surfaces) → K/J in parallel batches
(disjoint territory) → L last (G75 redaction is the largest single item and touches many
surfaces). Same orchestration pattern as Wave 5: shared files (prompt registry, llm_provider,
main.py, api.ts/types.ts) stay with the integrator; parallel agents get disjoint territory.
