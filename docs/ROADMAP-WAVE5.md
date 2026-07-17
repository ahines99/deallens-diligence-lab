# Wave 5 Roadmap — Real AI, still governed

**Status: 8 / 8 complete.** Same rules as `FEATURE_LEDGER.md` and `ROADMAP-WAVE4.md`: an item
is `done` only when its implementation **and** its acceptance test/artifact both exist in the
worktree, verified green (backend pytest + frontend vitest + ruff + tsc + lint).

**The thesis of this wave.** Waves 1–4 built a production-grade LLM *harness* — evidence
grounding, citation auditing, abstention, prompt manifests, judge evals, retrieval gates — around
mostly deterministic *engines* (signal-phrase scanning, lexical extraction, feature-hashing
embeddings). Wave 5 inverts the roles where an LLM genuinely beats rules: **the LLM becomes the
generator; the deterministic layer becomes the verifier, the offline fallback, and the eval
baseline.** Nothing an LLM produces enters the governed record unverified.

**Constraints carried forward (non-negotiable):**

- CI stays hermetic: `LLM_MODE=mock` keeps every test offline; every LLM-first path has a
  deterministic fallback that is also the mock-mode behavior.
- Fail closed, never silently: an unverifiable LLM output is dropped/rejected with a
  machine-readable reason, and the deterministic path serves the result.
- Every LLM-touched artifact binds a hashed prompt manifest (G10) and honest provenance.
- Consent gating is unchanged: external LLM requires workspace consent and a
  non-`restricted` data classification; confidential data-room content is NEVER sent to an
  external LLM without both.
- Keyless-by-default *data* remains; the pitch becomes "keyless data, bring-your-own-LLM."
  New runtime dependencies are allowed only as **optional extras** (G55) — the base install and
  CI stay dependency-stable.

---

## Theme F — LLM-first generation with deterministic verification

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G51 | Structured LLM substrate: `llm_provider.structured_llm(prompt_id, user, schema)` — JSON-constrained completion with the same consent/mock/no-key gating as polish, registry-hashed prompts, robust JSON extraction, Pydantic schema validation, fail-closed `StructuredOutcome` (data or None + machine-readable reason + manifest) | M | `done` — gating matrix, fenced/malformed-JSON and schema-mismatch fail-closed, provider-error fallback, manifest binding, Wave 5 prompt registration (`apps/api/tests/test_structured_llm.py`) |
| G52 | LLM-first risk extraction with verbatim-span verification: LLM reads filing chunks and proposes findings with exact quotes; a verifier accepts a finding ONLY if its quote appears verbatim (whitespace-normalized) in the source chunk and its category is in the taxonomy; verified findings flow into the existing evidence/risk pipeline; the signal-phrase scanner remains the mock/offline path and the recall baseline; sealed runs record which extractor ran | L | `done` — fabricated/paraphrased/recased-span rejection, category + severity validation, mock/no-consent fallback to the scanner with zero provider calls, `output_summary.risk_extraction` provenance in the sealed run, `compare_with_scanner` diff artifact (`apps/api/tests/test_llm_risk_extraction.py`) |
| G53 | Schema-constrained LLM claim extraction: the deal-intelligence extractor gains an LLM path that proposes structured claims with supporting quotes; the verifier requires the quote verbatim in the chunk AND the claimed value present within the quote (digit-boundary match, no scale inference) before an unreviewed `StructuredClaim` revision is minted (locator = the verified chunk); the pattern-based extractor remains fallback/baseline; the four-eyes human review loop is unchanged; consent-gated runs emit an `intelligence.claim_extraction` audit event with full engine provenance | L | `done` — fabricated quote/value rejection with per-proposal reasons, locator fidelity re-checked at approval, mock/no-consent/restricted byte-identical to the pattern path with zero provider calls, provider-failure fallback, distinct-reviewer rule intact (`apps/api/tests/test_llm_claim_extraction.py`) |
| G54 | Grounded synthesis for cross-corpus Q&A: extend the G04 fail-closed fluency pass to the deal-room/cross-corpus answer path, preserving public-vs-confidential citation labels; confidential quotes are sent to the LLM only when consent + classification allow; drift → extractive answer served | M | `done` — consent/classification gating incl. `restricted` (provider never constructed), audit-rejected drift fallback, abstention preserved, citations byte-identical in every path, `[PUBLIC]`/`[CONFIDENTIAL]` labeling asserted at the provider seam (`apps/api/tests/test_cross_corpus_synthesis.py`) |

## Theme G — Measured models

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G55 | Real neural embeddings as an optional local backend: `EMBEDDINGS_BACKEND=feature_hashing (default) | onnx_local` with `EMBEDDINGS_MODEL_PATH`; `onnxruntime` + `tokenizers` ship as an optional extra (`pip install .[embeddings]`) so the base install/CI are unchanged; the `embedding_id` method tag (G02) isolates vector spaces — retrieval filters stored vectors by the ACTIVE method and the backfill worker re-embeds stale-method rows; `embedding_service.embedding_status()` reports configured-vs-active backend honestly | L | `done` — backend routing + explicit degradation when the extra/model is absent, chunk tagging, cross-method retrieval isolation, stale-method backfill refresh + idempotency (`apps/api/tests/test_embedding_backends.py`; hybrid contract unchanged per `test_hybrid_retrieval.py`) |
| G56 | Model-quality dashboard: one backend aggregation (`GET /api/model-ops/quality`) joining judge-eval faithfulness by model/prompt, committed retrieval metrics + baselines, abstention calibration threshold, prompt manifests, and an extractor-comparison slot — each section with explicit source status; a `/quality` page renders it (global nav) | M | `done` — endpoint contract with per-section status and no fabricated zeros (`apps/api/tests/test_model_quality.py`), page renders per-section status + outage-as-warning (`apps/web/src/app/quality/page.test.tsx`) |

## Theme H — Agentic orchestration (stretch)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G57 | Diligence agent: a budget-capped, consent-gated tool-use loop (Anthropic tools API) over a curated allowlist of governed READ-ONLY/pure-compute workspace tools (overview, filing search, cited Q&A, risks, evidence, saved cases, in-memory underwriting scenarios). The harness scopes every call; the agent cannot write to governed records (the four-eyes boundary holds for agents too). Every run seals an append-only `agent_run` ArtifactVersion with the full transcript; deal-linked workspaces also emit an `agent.run_completed` audit event. The final answer passes a fail-closed grounding gate: any quantity token or EV-### ref no tool result produced withholds the answer. `/agent` workbench tab renders the transcript + grounding verdict | XL | `done` — grounded completion + sealing, fabricated-number rejection with the transcript still sealed, unknown-tool/bad-args as recorded errors, step-budget fail-closed, mock/no-consent/restricted never construct a provider, pure-compute scenario tool, route contract + G58 quota classification (`apps/api/tests/test_diligence_agent.py`); console renders completed/rejected/not_run honestly (`apps/web/src/components/workbench/AgentConsole.test.tsx`) |
| G58 | Live-LLM demo hardening: per-org LLM-call quota bucket over the existing `_OrgQuotaLimiter` (`ORG_LLM_QUOTA_PER_HOUR`, metered only when `LLM_MODE=live`), applied to every route that can trigger a live LLM call; deploy runbook updated so the public demo can enable synthesis without an open-ended API bill | S | `done` — live-only metering, per-org boundary, deterministic endpoints unaffected, Retry-After contract (`apps/api/tests/test_quotas.py::test_llm_quota_meters_live_mode_only`); `docs/deploy-demo.md` §"Enabling the live LLM" |

**Sequencing (as delivered):** G51 first (everything depends on it) → G52/G53/G54 in parallel
(disjoint territory; prompts pre-registered centrally in G51) with G55/G58 alongside → G56 →
G57 last.
