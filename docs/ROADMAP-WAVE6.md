# Wave 6 Roadmap — 25 capabilities

**Status: 25 / 25 complete.** Delivered in four batches (M, I, K/J, L), each verified green before the next. Same rules as `FEATURE_LEDGER.md` and prior roadmaps: an item is
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
| G59 | Agent-drafted IC memo sections: the agent drafts one memo section at a time from governed tool results; EVERY section passes the grounding gate independently (a rejected section is withheld, others survive); the analyst accepts/rejects per section in the UI and only accepted sections enter the draft memo — human-in-the-loop by construction | L | `done` — per-section runs each grounding-gated independently (fabricated section withheld, grounded siblings survive), append-only decision supersessions, assembled markdown from ACCEPTED sections only, actor-required decisions, mock/no-consent not_run with nothing persisted (`apps/api/tests/test_agent_memo.py`) |
| G60 | Agent proposals into the four-eyes queues: the agent may PROPOSE QoE adjustments and structured claims (flagged as agent-proposed with the run's manifest bound); proposals land in the existing review inbox; approval remains human-only — an agent-proposed item can never be agent-approved (extends the trusted-service reviewer ban) | M | `done` — propose_qoe_adjustment/propose_claim tools minting as `agent:diligence` with G53-verifier reuse (verbatim quote + value-in-quote or nothing minted), review-inbox surfacing with four-eyes self-exclusion, agent/trusted-service provably rejected as approver, human approval unchanged (`apps/api/tests/test_agent_proposals.py`) |
| G61 | Streaming agent runs over the existing SSE substrate: the console renders the step timeline live (tool call → result → next round) and survives reconnect mid-run; the sealed artifact remains the source of truth after completion | M | `done` — started/tool_step/finished(+error) SSE frames with the terminal frame carrying the FULL sealed record, thread-private session discipline, non-resumable-by-design reconnect via GET agent/runs (sealed transcript = replay source), console streams live and never double-runs on a drop (`apps/api/tests/test_agent_stream.py`, `AgentConsole.test.tsx`) |
| G62 | Agent evaluation harness: a committed golden set of objectives with expected tool-selection patterns and grounding outcomes; measures tool-choice sanity, grounding pass rate, and abstention correctness with scripted providers; CI-gated and surfaced as a `/quality` section | L | `done` — 8 committed golden objectives over scripted providers (honestly framed: measures the harness pipeline, never model intelligence), four metrics with a committed baseline + CI floor, corrupted-expectation detection, `/quality` agent_evals section (`apps/api/tests/test_agent_eval.py`) |
| G63 | Comparative agent runs: one objective across the target workspace plus selected comp workspaces; each tool call remains harness-scoped to its own workspace; the merged answer carries per-workspace provenance and the grounding gate runs against the union of that run's tool results only | L | `done` — per-workspace sealed runs with sentinel-based scoping proof, unanimous-consent fail-closed naming the blocking workspace, DETERMINISTIC merge (no second LLM pass — documented) with per-workspace sections and withheld/failed honesty, belt-and-braces union grounding, sealed agent_comparative_run artifact (`apps/api/tests/test_agent_compare.py`) |

## Theme J — Peer & market context (5)

Same keyless public data, one level up: how the target sits in its universe.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G64 | XBRL frames peer benchmarking: pull the SEC frames API for the target's SIC peers and report percentile ranks for growth/margins/leverage with explicit coverage counts (peers reporting the concept) — thin frames degrade to "insufficient peer coverage", never a fabricated percentile | L | `done` — midrank percentiles vs the honest full frames universe (peer_scope states frames carry no SIC), 20-entity coverage floor, both-years growth join, per-metric degradation, live tests auto-skip offline (`apps/api/tests/test_peer_benchmark.py`) |
| G65 | Sum-of-the-parts valuation from G12 segments: per-segment multiples (user-supplied or benchmarked) over segment revenue/EBITDA, reconciled exactly to the consolidated total with an explicit unallocated/eliminations residual — never force-balanced | M | `done` — per-segment implied EV with explicit residual (valued only with a residual multiple; never force-balanced), G12 partial propagation, consolidated-only unavailable (`apps/api/tests/test_sotp.py`) |
| G66 | Buyback & dilution analysis: shares-outstanding trend, SBC expense vs repurchases, net dilution rate per fiscal year from XBRL with citations; missing concepts degrade per-year, never interpolated | M | `done` — CY-frame-keyed per-year shares/SBC/repurchases/net dilution across CONSECUTIVE years only, per-field-year citations, missing-concept honesty (`apps/api/tests/test_dilution.py`) |
| G67 | Litigation & proceedings: Item 3 extraction from 10-Ks plus the 8-K legal-events timeline, chunked with citations and surfaced as a risk-flag source (degraded-source discipline like the other extensions) | M | `done` — Item 3 extraction bounded Item 4/Item 5 with the (?![0-9a-z]) discipline, explicitly-legal 8-K codes with the 8.01 limitation stated, risk-flag source wired into the analysis pipeline, unavailable-not-clean (`apps/api/tests/test_litigation.py`) |
| G68 | Macro-linked Monte Carlo presets: documented mappings from FRED series (rates, spreads, GDP) to driver distributions the user can load and edit before a run; the mapping is a transparent, versioned config — never a hidden model | M | `done` — versioned v1 mappings (DGS10/BAA10Y/GDP) with documented reviewable coefficients, per-series omission on outage/thin history, distributions schema-validated against MonteCarloRequest (`apps/api/tests/test_macro_presets.py`) |

## Theme K — Underwriting depth (5)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G69 | One-way sensitivity tornado: rank every driver's ± impact on IRR/MoIC around the base case (deterministic, reusing `_apply_variable`), rendered as a tornado chart in the stress workbench | S | `done` — ranked ± driver impacts with per-variable shift conventions, inevaluable-extreme honesty, hand-verified deltas, tornado card in the stress workbench (`apps/api/tests/test_underwriting_wave6.py`) |
| G70 | Dividend recap solver: maximum distribution at a chosen date subject to leverage / coverage / minimum-liquidity constraints, solved by bisection over the existing engine with the binding constraint named — no solution reports WHY | M | `done` — bisection over the real engine with binding-constraint attribution (hand-solved fixtures 236.0/436.0 exact), infeasible/unbounded honesty (`apps/api/tests/test_underwriting_wave6.py`) |
| G71 | Working-capital facility sizing: peak intra-year revolver need from the G25 seasonality model (monthly working-capital swings vs the annual projection), with the peak month and headroom vs the modeled commitment | M | `done` — seasonality-scaled peak intra-year draw with headroom sign conventions and no-data unavailability (`apps/api/tests/test_underwriting_wave6.py`) |
| G72 | Fund-level Monte Carlo: aggregate per-deal simulations with shared macro factor draws so deal outcomes correlate through common factors (documented loadings, user-editable); fund construction integration for portfolio IRR/MoIC bands | XL | `done` — shared macro factor draws + idiosyncratic draws under one seed (byte-identical reruns), fund/deal bands, correlation_effect shows correlated p5 ≤ independent p5 deterministically, fund-construction integration with named exclusions (`apps/api/tests/test_underwriting_wave6.py`) |
| G73 | Year-by-year value-creation waterfall: the G22 attribution bridge decomposed per hold year (EBITDA growth, multiple, deleveraging, cross term per year), reconciling exactly to the total bridge | M | `done` — per-year Decimal reconciliation, final-year multiple allocation, telescoping to the exact G22 total (`apps/api/tests/test_underwriting_wave6.py`) |

## Theme L — Trust & collaboration (5)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G74 | Signed export attestation: Ed25519 signatures over the export/bundle manifest (key from config, public key served), verifiable fully offline; tampering any byte fails verification — extends the existing hash discipline to cryptographic provenance | M | `done` — Ed25519 over the exact canonical bytes the hashes cover (never self-referential), tamper → attestation_signature failure alone even on a re-hashed forgery, VERIFY.md offline recipe in the bundle, key-absent hash-only honesty (`apps/api/tests/test_export_signing.py`) |
| G75 | Data-room redaction workflow: span-level redactions proposed on a document version and approved four-eyes; approval mints a NEW immutable redacted version (originals untouched); QA, search, and share-surfaces serve the redacted version to non-privileged viewers | XL | `done` — per-chunk span addressing, ORM-guarded single proposed→decided transition, four-eyes with trusted-service banned both sides, approval mints version N+1 via standard supersession ([REDACTED] spliced; originals byte-identical), every latest-wins read surface serves the redacted version with zero special-casing, binary v1 boundary documented (`apps/api/tests/test_redaction.py`) |
| G76 | Share-link analytics + watermarking: per-link view events (when, coarse where), a visible per-link watermark rendered into shared views, and one-click revocation surfaced with the analytics | M | `done` — append-only coarse view events recorded only on successful public reads (best-effort, never breaks the share), owner analytics with revocation surfaced, server-composed watermark rendered on the new public shared page (`apps/api/tests/test_share_link_analytics.py`) |
| G77 | Notification digests: per-user in-app daily/weekly digest rolling up watchlist filings, audit events, and review-inbox aging (no email dependency); directed-notification privacy rules carry over | S | `done` — on-read daily/weekly digests with directed-notification privacy carried over, no read_at mutation, inbox + SLA rollup embedded (`apps/api/tests/test_notification_digest.py`) |
| G78 | Review-inbox SLAs: per-plane aging thresholds with an aging report (oldest items, breach counts) and digest/notification hooks when an item crosses its SLA | S | `done` — per-plane aging with documented default SLAs, four-eyes exclusions hold in the aging view, per-request overrides (`apps/api/tests/test_inbox_sla.py`) |

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
