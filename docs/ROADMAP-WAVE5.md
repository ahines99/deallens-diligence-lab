# Wave 5 Roadmap — Real AI, still governed

**Status: in progress.** Same rules as `FEATURE_LEDGER.md` and `ROADMAP-WAVE4.md`: an item is
`done` only when its implementation **and** its acceptance test/artifact both exist in the
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
| G51 | Structured LLM substrate: `llm_provider.structured_llm(prompt_id, user, schema)` — JSON-constrained completion with the same consent/mock/no-key gating as polish, registry-hashed prompts, robust JSON extraction, Pydantic schema validation, fail-closed `StructuredOutcome` (data or None + machine-readable reason + manifest) | M | planned — gating matrix, malformed-JSON and schema-mismatch fail-closed, mock determinism, manifest binding |
| G52 | LLM-first risk extraction with verbatim-span verification: LLM reads filing chunks and proposes findings with exact quotes; a verifier accepts a finding ONLY if its quote appears verbatim (whitespace-normalized) in the source chunk and its category is in the taxonomy; verified findings flow into the existing evidence/risk pipeline; the signal-phrase scanner remains the mock/offline path and the recall baseline; sealed runs record which extractor ran | L | planned — fabricated-span rejection, category/severity validation, mock-mode falls back to scanner, provenance in sealed run, side-by-side comparison artifact (LLM vs scanner on the same fixture chunks) |
| G53 | Schema-constrained LLM claim extraction: the deal-intelligence extractor gains an LLM path that proposes structured claims with supporting quotes; the verifier requires the quote verbatim in the chunk AND the claimed value present within the quote before an unreviewed `StructuredClaim` revision is minted (locator = the verified chunk); the pattern-based extractor remains fallback/baseline; the four-eyes human review loop is unchanged | L | planned — fabricated quote/value rejection, locator fidelity, mock-mode stays pattern-based, review loop still enforces distinct reviewer |
| G54 | Grounded synthesis for cross-corpus Q&A: extend the G04 fail-closed fluency pass to the deal-room/cross-corpus answer path, preserving public-vs-confidential citation labels; confidential quotes are sent to the LLM only when consent + classification allow; drift → extractive answer served | M | planned — consent/classification gating incl. `restricted`, drift rejection, abstention preserved, labels intact byte-for-byte |

## Theme G — Measured models

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G55 | Real neural embeddings as an optional local backend: `EMBEDDINGS_BACKEND=feature_hashing (default) | onnx_local` with `EMBEDDINGS_MODEL_PATH`; `onnxruntime` + tokenizer ship as an optional extra (`pip install .[embeddings]`) so the base install/CI are unchanged; the `embedding_id` method tag (G02) isolates vector spaces — mixed-method vectors are never compared, backfill re-embeds on method change; the retrieval eval harness reports per-backend metrics when the backend is available | L | planned — backend selection + graceful fallback when the extra/model is absent, method-tag isolation, hybrid retrieval contract unchanged, eval-harness per-backend hook |
| G56 | Model-quality dashboard: one backend aggregation (`GET /api/model-ops/quality`) joining judge-eval faithfulness by model/prompt, committed retrieval metrics + baselines, abstention calibration threshold, prompt manifests, and G52's extractor comparison — each section with explicit source status; a `/quality` page renders it | M | planned — endpoint contract test with per-section status (absent data reads `unavailable`, never zeros), vitest page test |

## Theme H — Agentic orchestration (stretch)

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G57 | Diligence agent: a tool-use loop (Anthropic tools API) over the workbench's governed services — ingest, extract, run cases, draft memo — where every step lands in the audit outbox with actor attribution, budget-capped and consent-gated | XL | planned |
| G58 | Live-LLM demo hardening: per-org LLM-call quota bucket over the existing `_OrgQuotaLimiter`, applied to every route that can trigger a live LLM call; deploy runbook updated so the public demo can enable synthesis without an open-ended API bill | S | planned |

**Sequencing:** G51 first (everything depends on it) → G52/G53/G54 in parallel (disjoint
territory; prompts pre-registered centrally in G51) → G56 → G55 → G58 → G57.
