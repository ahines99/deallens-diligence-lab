# Wave 4 Roadmap — 50 planned capabilities

Planning ledger for the next development wave. Same rules as `FEATURE_LEDGER.md`: an item is
`done` only when its implementation **and** the named acceptance evidence exist in the worktree.
Sequenced by portfolio value; items flip to `done` as they land with their acceptance evidence.

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
| G01 | Hybrid retrieval: pgvector-ready embeddings fused with BM25 via reciprocal-rank fusion; deterministic local keyless embedding (feature-hashing, no ML deps) so the default stays keyless | L | `done` — fusion-ranking + BM25-contract tests (`apps/api/tests/test_hybrid_retrieval.py`: `test_rrf_ranks_a_chunk_both_rankers_like_above_a_single_ranker_favorite`, `test_hybrid_returns_fused_results_when_embeddings_exist`, `test_retrieve_bm25_signature_and_return_shape_unchanged`, `test_hybrid_falls_back_to_bm25_when_no_embeddings`) |
| G02 | Embedding ingestion pipeline: chunk embeddings persisted at ingest (`DocumentChunk.embedding` vector + `embedding_id` method tag wired), plus a backfill worker for existing workspaces | M | `done` — ingest + backfill idempotency tests (`apps/api/tests/test_hybrid_retrieval.py`: `test_embed_is_deterministic_and_l2_normalized`, `test_cosine_high_for_identical_low_for_unrelated`, `test_empty_text_is_zero_vector_and_cosine_zero`, `test_ingest_persists_embeddings`, `test_backfill_fills_only_nulls_and_is_idempotent`) |
| G03 | Retrieval evaluation harness: golden question set over fixture filings; recall@k and MRR computed in CI and tracked in a committed metrics file | M | `done` — committed golden set (`src/eval/fixtures/golden_set.json`) + baseline (`src/eval/retrieval_metrics.json`); CI regression gate (`apps/api/tests/test_retrieval_eval.py`: `test_eval_computes_recall_and_mrr_for_every_ranker`, `test_metrics_meet_absolute_quality_floors`, `test_no_regression_below_committed_baseline`, `test_hybrid_never_underperforms_bm25`) |
| G04 | Grounded synthesis mode (live LLM): fluent answers composed **only** from retrieved extracts, gated by the citation auditor, abstention preserved | L | `done` — adversarial fail-closed tests (`apps/api/tests/test_grounded_synthesis.py`: `test_faithful_rewrite_is_applied_and_records_manifest`, `test_fabricated_number_is_rejected_and_extractive_answer_is_served`, `test_fabricated_citation_is_rejected_and_extractive_answer_is_served`, `test_empty_rewrite_falls_back_to_extractive`, `test_abstention_is_preserved_and_llm_never_called`, `test_no_consent_stays_extractive`, `test_mock_mode_stays_extractive`, `test_ask_default_is_purely_extractive`, `test_ask_grounded_without_consent_stays_extractive`) |
| G05 | LLM-as-judge faithfulness evals with persisted eval runs and a quality dashboard per model/prompt version | L | `done` — mock-judge + persistence/quality-view tests (`apps/api/tests/test_judge_evals.py`: `test_mock_judge_passes_a_grounded_answer`, `test_mock_judge_flags_an_unsupported_number`, `test_mock_judge_flags_an_unsupported_citation`, `test_answer_with_no_checkable_claims_is_trivially_faithful`, `test_persisted_judgment_roundtrips_with_provenance`, `test_quality_summary_groups_by_model_and_prompt`) |
| G06 | Abstention calibration: score distributions for answered vs abstained questions; thresholds justified by a committed calibration study | M | `done` — committed study (`src/eval/calibration_study.md`) + runner (`src/eval/calibration.py`); boundary + drift-guard tests (`apps/api/tests/test_calibration.py`: `test_service_threshold_matches_calibrated_value`, `test_coverage_at_threshold_is_answered`, `test_coverage_just_below_threshold_flips_to_partial`, `test_no_overlap_question_abstains`, `test_calibration_classes_are_separable_at_the_threshold`) |
| G07 | Cross-year 10-K semantic diff: risk-factor drift (added / removed / materially changed) with citations into both filings | L | `done` — embedding-alignment drift classification on fixture year-pairs (`apps/api/tests/test_filing_diff.py`: `test_risk_diff_classifies_added_removed_and_changed`, `test_risk_diff_endpoint_contract`, `test_single_filing_is_unavailable_not_fabricated`) |
| G08 | Unified cross-corpus Q&A: one question over filings + data-room docs with provenance-aware citations (public vs confidential clearly labeled) | M | `done` — mixed-corpus citation labeling + abstention (`apps/api/tests/test_cross_corpus_qa.py`: `test_answer_cites_both_corpora_with_confidentiality_labels`, `test_filings_only_workspace_answers_labeled_public`, `test_abstains_when_neither_corpus_matches`, `test_cross_corpus_endpoint_contract`) |
| G09 | Embedding-similarity comp discovery from business descriptions, shown side-by-side with the SIC-code method and its disagreements | M | `done` — similarity-ranking + disagreement + determinism + endpoint-contract tests (`apps/api/tests/test_comp_similarity.py`: `test_similarity_orders_peers_by_closeness_to_target`, `test_disagreements_surface_embedding_only_and_sic_only`, `test_peer_in_both_sets_is_not_a_disagreement`, `test_missing_target_description_is_unavailable_not_fabricated`, `test_no_peer_descriptions_is_unavailable`, `test_undescribed_peer_is_excluded_not_scored_zero`, `test_ranking_is_deterministic`, `test_equal_similarity_ties_break_by_ticker`, `test_similarity_endpoint_returns_comparison_contract`) |
| G10 | Prompt & model-config registry: versioned, hashed prompt manifests bound to every LLM-touched artifact run (reproducible LLM ops) | M | `done` — hash round-trip + tamper-detection + run-binding tests (`apps/api/tests/test_prompt_registry.py`: `test_manifest_hash_is_the_sha256_of_the_registered_template`, `test_grounded_synthesis_prompt_is_registered_and_hashed`, `test_changing_the_template_text_changes_the_hash`, `test_prompt_manifest_binds_into_a_sealed_llm_run`, `test_deterministic_run_has_no_prompt_manifest`, `test_prompt_manifest_endpoint_lists_registered_prompts`) |

## Theme B — Public-data research depth (10)

Widens the moat of "real data, no keys" — the research-analyst credibility layer.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G11 | 10-Q quarterly ingestion + trailing-twelve-month metric derivation | M | `done` — TTM arithmetic tests across fiscal-year boundaries (`apps/api/tests/test_quarterly_ttm.py`) |
| G12 | XBRL segment-level revenue (dimensional facts) with segment trend charts | L | `done` — dimensional-member extraction, consolidated-only "unavailable" (never fabricated), reconciliation-based `partial`, trend ordering + endpoint contract (`apps/api/tests/test_segments.py`) |
| G13 | DEF 14A proxy ingestion: executive compensation table + governance red flags (staggered board, dual-class) | L | `done` — Summary Compensation Table parse into NEO rows with missing-value-stays-`None` (never imputed), governance red-flag heuristics (staggered/classified board fires on "classified board"/"three classes" and stays quiet on clean text, dual-class super-voting, combined CEO/Chair, poison pill), EDGAR-outage → `unavailable` (never false-clean), and store+get endpoint roundtrip (`apps/api/tests/test_proxy_governance.py`) |
| G14 | 13F institutional ownership snapshot + holder-concentration analysis | M | `done` — parse + concentration math tests (`apps/api/tests/test_ownership.py`: `test_parse_13f_infotable_reads_all_positions`, `test_parse_13f_infotable_malformed_returns_empty`, `test_concentration_math_is_hand_verified`, `test_concentration_is_scale_invariant_and_single_holding_is_one`, `test_concentration_excludes_missing_and_nonpositive_values_never_imputes`, `test_concentration_empty_is_none_not_zero`, `test_parse_and_concentration_compose_end_to_end`, `test_institutional_ownership_outage_is_unavailable_not_clean_empty`, `test_non_manager_target_is_not_applicable_with_honest_note`, `test_manager_target_reports_portfolio_concentration`) |
| G15 | 13D/13G activist-stake detection wired into the signals timeline | S | `done` — event classification tests (`apps/api/tests/test_ownership.py`: `test_classify_13d_is_activist_and_13g_is_passive`, `test_classify_marks_amendments_and_tolerates_form_variants`, `test_build_stake_event_preserves_missing_fields_as_none`, `test_cover_page_extraction_of_filer_and_percent`, `test_activist_stakes_outage_is_unavailable_not_clean_empty`, `test_activist_stakes_classifies_submissions_into_timeline`) |
| G16 | Debt maturity schedule extraction from filings + maturity-wall chart | L | `done` — schedule extraction + never-impute gaps + endpoint/legacy + maturity-wall-flag tests (`apps/api/tests/test_debt_maturities.py`: `test_all_buckets_tagged_yields_full_ordered_schedule_and_total`, `test_missing_bucket_is_reported_and_never_imputed`, `test_no_maturity_concepts_is_unavailable_not_clean_empty`, `test_partial_status_when_only_some_buckets_present`, `test_only_latest_balance_sheet_date_populates_the_schedule`, `test_amendment_precedence_keeps_latest_filed_value`, `test_debt_maturities_endpoint_contract`, `test_debt_maturities_endpoint_unavailable_before_refresh`, `test_near_term_maturity_wall_flag_fires_when_y1_y2_dominate`, `test_no_maturity_wall_flag_when_back_loaded`, `test_no_maturity_wall_flag_on_partial_schedule`) |
| G17 | Fiscal-period consistency diagnostics (carryover F41): mixed-period operands flagged, never silently blended | M | `done` — mismatch detection tests (`apps/api/tests/test_fiscal_diagnostics.py`) |
| G18 | Consolidated signals overview page (carryover F55): one screen aggregating events/insiders/news/themes with per-source status | S | page/API rendering test |
| G19 | Watchlists with scheduled refresh: track N companies, detect new filings, emit notification/webhook events through the existing outbox | M | scheduler + dedup + outbox event tests |
| G20 | Insider-pattern analytics: clustered buying/selling windows, 10b5-1 plan flags, officer-vs-director splits | M | `done` — clustering + classification tests (`apps/api/tests/test_insider_patterns.py`: `test_adjacent_same_direction_trades_form_one_cluster`, `test_gap_over_threshold_splits_clusters`, `test_buys_and_sells_never_merge_into_one_window`, `test_plan_summary_counts_planned_discretionary_and_unknown`, `test_missing_plan_flag_is_unknown_never_discretionary`, `test_role_split_sums_buys_and_sells_by_role`, `test_unavailable_feed_is_unavailable_not_clean_zero`, `test_parse_form4_flags_10b5_1_from_footnote_and_reads_roles`, `test_parse_form4_document_level_10b5_1_checkbox`, `test_parse_form4_no_10b5_1_indicator_is_unknown`) |

## Theme C — Underwriting & quantitative depth (10)

The PE-domain fluency layer — complex, and exactly what a diligence/valuation interviewer probes.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G21 | Monte Carlo LBO: driver distributions, percentile IRR/MoIC bands, deterministic seeding so runs are reproducible | L | **done** — `tests/test_monte_carlo_attribution.py::test_same_seed_is_byte_identical_and_different_seed_moves_the_median`, `::test_monte_carlo_percentiles_are_ordered_and_iterations_are_accounted_for`, `::test_zero_variance_distributions_collapse_to_the_deterministic_result`, `::test_monte_carlo_validation_rejects_bad_iterations_and_unknown_drivers` |
| G22 | Returns attribution bridge: entry/exit multiple vs deleveraging vs EBITDA growth decomposition, reconciling exactly to total return | M | **done** — `tests/test_monte_carlo_attribution.py::test_attribution_components_sum_exactly_to_total_value_creation`, `::test_attribution_endpoint_reconciles` |
| G23 | Covenant headroom projection: quarter-by-quarter headroom under each case with breach-quarter detection | M | **done** — `tests/test_underwriting_analytics.py::test_covenant_headroom_breach_boundary_is_the_threshold_crossing_quarter`, `::test_covenant_headroom_endpoint_and_requires_a_covenant`, `::test_covenant_headroom_rejects_invalid_assumptions` |
| G24 | Driver-based operating model: user-defined drivers with formula validation, cycle detection, and provenance on every derived line | L | formula parser + cycle rejection tests |
| G25 | Working-capital seasonality modeling from monthly imports (peg by month, not annual average) | M | seasonal peg tests on fixture monthlies |
| G26 | Dividend recap and bolt-on acquisition modeling inside case versions | L | sources/uses + returns integration tests |
| G27 | Management-vs-sponsor case variance analysis: line-level deltas with materiality ranking | S | **done** — `tests/test_underwriting_analytics.py::test_case_variance_lines_reconcile_and_rank_by_materiality`, `::test_case_variance_endpoint_compares_persisted_cases`, `::test_case_variance_operand_requires_exactly_one_source` |
| G28 | Exit readiness scorecard + hold-period sensitivity (3/5/7-year grids) | M | **done** — `tests/test_underwriting_analytics.py::test_exit_readiness_scorecard_names_thresholds_and_grids_holds`, `::test_exit_readiness_endpoint` |
| G29 | Fund-level portfolio construction: aggregated exposure vs concentration limits, simple pacing model | L | limit-breach detection tests |
| G30 | Valuation football field: triangulation methods on one chart with explicit method weights and excluded-method reasons | S | **done** — `tests/test_underwriting_analytics.py::test_football_field_weights_sum_to_one_and_excluded_methods_carry_reasons`, `::test_football_field_endpoint_and_requires_a_method` |

## Theme D — Platform engineering & scale (10)

The senior-engineer credibility layer: the app already works; this makes it *operable*.

| ID | Capability | Effort | Acceptance evidence |
|---|---|---|---|
| G31 | Durable job queue: workspace builds move from in-process BackgroundTasks to a DB-backed job table + worker with retries, heartbeats, and stale-claim recovery (generalizes the webhook outbox pattern) | L | **done** — `tests/test_job_queue.py::test_two_workers_claim_exactly_once`, `::test_stale_claim_is_recovered_and_reprocessed`, `::test_stale_claim_on_final_attempt_goes_dead`, `::test_transient_failures_retry_until_success`, `::test_exhausted_attempts_mark_job_dead_with_last_error`, `::test_workspace_build_completes_through_job_queue`, `::test_worker_batch_processes_queued_jobs` |
| G32 | Server-sent events for live build progress and notifications (polling kept as fallback) | M | **done** — `tests/test_sse.py::test_build_events_stream_yields_ready_frame`, `::test_build_events_cross_org_is_404`, `::test_iter_build_events_emits_each_transition_once_until_ready`, `::test_iter_build_events_times_out_without_hanging` |
| G33 | In-app notification center fed by the existing audit outbox | M | **done** — `tests/test_notifications.py::test_audit_events_map_to_notifications_with_titles`, `::test_sync_is_idempotent_and_dedups_by_source_event`, `::test_mark_read_flips_read_at_and_unread_count`, `::test_notifications_are_tenant_scoped`, `::test_mark_read_cross_org_is_not_found`, `::test_notification_endpoints_via_api` |
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
