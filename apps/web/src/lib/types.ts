// DealLens shared types — mirror of docs/CONTRACTS.md. Keep in sync with the backend schemas.

export type DealType =
  | "buyout"
  | "growth_equity"
  | "private_credit"
  | "public_equity"
  | "govcon"
  | "software_platform";

export type WorkspaceStatus = "draft" | "in_progress" | "complete";
export type TargetType = "public_company" | "private_company" | "synthetic_private";
export type Severity = "low" | "medium" | "high" | "critical";
export type Priority = "low" | "medium" | "high";
export type ClaimType = "fact" | "calculation" | "inference" | "assumption";
export type MemoType = "ic_memo" | "bear_case";

export type RiskCategory =
  | "customer_concentration"
  | "supplier_concentration"
  | "demand_weakness"
  | "margin_pressure"
  | "debt_liquidity"
  | "legal_regulatory"
  | "cyber_security"
  | "integration_ma"
  | "ai_tech_disruption"
  | "govcon_risk";

export type Workstream =
  | "commercial"
  | "product_technology"
  | "financial"
  | "customer"
  | "market"
  | "legal_regulatory"
  | "cybersecurity"
  | "ai_data"
  | "management"
  | "govcon";

export interface Workspace {
  id: string;
  name: string;
  organization_id: string | null;
  target_id: string | null;
  deal_type: DealType;
  investment_question: string;
  status: WorkspaceStatus;
  data_classification: WorkspaceDataClassification;
  external_llm_allowed: boolean;
  build_status: WorkspaceBuildState;
  build_step: WorkspaceBuildStep | null;
  build_error: string | null;
  created_at: string;
  updated_at: string;
}

export type WorkspaceBuildState = "ready" | "building" | "failed";

export type WorkspaceBuildStep =
  | "resolving_company"
  | "fetching_financials"
  | "indexing_filings"
  | "fetching_annual_report"
  | "running_analysis";

export interface WorkspaceBuildStatus {
  workspace_id: string;
  status: WorkspaceBuildState;
  step: WorkspaceBuildStep | null;
  error: string | null;
  ticker: string | null;
}

export type WorkspaceDataClassification = "public" | "internal" | "confidential" | "restricted";

export interface Target {
  id: string;
  name: string;
  target_type: TargetType;
  ticker: string | null;
  cik: string | null;
  sector: string;
  description: string;
  revenue: number | null;
  revenue_growth: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_income: number | null;
  net_margin: number | null;
  rnd_pct: number | null;
  rule_of_40: number | null;
  cash: number | null;
  total_debt: number | null;
  headcount: number | null;
  fiscal_year_end: string | null;
  data_source: string;
  is_synthetic: boolean;
  created_at: string;
  updated_at: string;
}

export interface Filing {
  id: string;
  workspace_id: string;
  company_name: string;
  ticker: string | null;
  cik: string | null;
  form_type: string;
  filing_date: string;
  accession_number: string | null;
  document_url: string | null;
  section_count: number;
  is_synthetic: boolean;
  created_at: string;
}

export interface ComparableCompany {
  id: string;
  workspace_id: string;
  ticker: string;
  company_name: string;
  sector: string;
  business_description: string;
  revenue: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  revenue_growth: number | null;
  rnd_pct: number | null;
  market_cap: number | null;
  enterprise_value: number | null;
  ev_revenue_multiple: number | null;
  notes: string;
  data_source: string;
  is_illustrative: boolean;
}

export interface Evidence {
  id: string;
  workspace_id: string;
  ref: string;
  claim: string;
  claim_type: ClaimType;
  source_name: string;
  source_type: string;
  source_url: string | null;
  source_date: string | null;
  source_section: string | null;
  evidence_text: string;
  confidence: number;
  agent_name: string;
  created_at: string;
}

export interface RiskFinding {
  id: string;
  workspace_id: string;
  risk_category: RiskCategory;
  risk_category_label: string;
  title: string;
  finding: string;
  severity: Severity;
  severity_score: number;
  likelihood: Priority;
  confidence: number;
  evidence_ref: string | null;
  follow_up_question: string;
  workstream_owner: Workstream;
  created_at: string;
}

export interface DiligenceQuestion {
  id: string;
  workspace_id: string;
  workstream: Workstream;
  workstream_label: string;
  question: string;
  rationale: string;
  priority: Priority;
  evidence_ref: string | null;
  created_at: string;
}

export interface PlanWorkstream {
  workstream: Workstream;
  workstream_label: string;
  objective: string;
  key_questions: string[];
  evidence_needed: string[];
  status: "planned" | "in_progress" | "complete";
}

export interface DiligencePlan {
  workspace_id: string;
  investment_question: string;
  summary: string;
  workstreams: PlanWorkstream[];
  generated_at: string;
}

export interface BenchmarkMetric {
  key: string;
  label: string;
  unit: "pct" | "x" | "usd" | "ratio";
  target_value: number | null;
  peer_median: number | null;
  peer_min: number | null;
  peer_max: number | null;
  assessment: "above" | "in_line" | "below" | "n/a";
  commentary: string;
}

export interface FinancialBenchmark {
  workspace_id: string;
  target_name: string;
  peer_count: number;
  summary: string;
  metrics: BenchmarkMetric[];
  notes: string[];
  generated_at: string;
}

export interface Memo {
  id: string;
  workspace_id: string;
  memo_type: MemoType;
  title: string;
  markdown_content: string;
  created_at: string;
  updated_at: string;
}

export interface UnsupportedClaim {
  claim: string;
  why_weak: string;
  recommended_action: string;
}

export interface MissingEvidence {
  item: string;
  why_it_matters: string;
  workstream: Workstream;
}

export interface RedTeamQuestion {
  workstream: Workstream;
  workstream_label: string;
  question: string;
  rationale: string;
  priority: Priority;
}

export interface RedTeam {
  id: string;
  workspace_id: string;
  bear_case_markdown: string;
  summary: string;
  unsupported_claims: UnsupportedClaim[];
  missing_evidence: MissingEvidence[];
  high_priority_questions: RedTeamQuestion[];
  created_at: string;
}

export interface WorkspaceOverview {
  workspace: Workspace;
  target: Target | null;
  counts: {
    filings: number;
    comps: number;
    risks: number;
    questions: number;
    evidence: number;
  };
  artifacts: {
    plan: boolean;
    risks: boolean;
    questions: boolean;
    ic_memo: boolean;
    bear_case: boolean;
  };
  top_risks: RiskFinding[];
}

export interface SecSearchResult {
  cik: string;
  ticker: string;
  name: string;
}

export interface ExampleDealResult {
  organization_id: string;
  fund_id: string;
  deal_id: string;
  workspace_id: string;
  deal_code: string;
  import_status: string;
  open_exceptions: number;
}

export interface ExampleTemplateInfo {
  name: string;
  description: string;
}

export interface FilingsQACitation {
  filing_id: string;
  form_type: string | null;
  filing_date: string | null;
  section: string;
  document_url: string | null;
  quote: string;
  retrieval_score: number;
}

export interface FilingsQAResult {
  workspace_id: string;
  question: string;
  status: "answered" | "partial" | "abstained";
  answer: string;
  citations: FilingsQACitation[];
  retrieval: {
    chunks_considered: number;
    matched_terms: string[];
    coverage?: number;
    abstention_reason: string | null;
  };
  method: string;
  generated_at: string;
}

export interface MemoFaithfulnessDocument {
  document_type: string;
  citation_count: number;
  distinct_refs: number;
  unresolved_refs: string[];
  numeric_token_count: number;
  uncited_numeric_sentences: string[];
  uncited_numeric_sentence_count: number;
  fully_resolved: boolean;
}

export interface MemoFaithfulnessReport {
  workspace_id: string;
  evidence_ref_count: number;
  documents: MemoFaithfulnessDocument[];
  generated_at: string;
}

export interface HealthStatus {
  status: string;
  llm_mode: string;
  database: string;
  demo_mode?: boolean;
}

// --- Roadmap extensions: trends, macro, GovCon -----------------------------

export interface TrendPoint {
  year: string;
  revenue: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  rnd_pct: number | null;
}

export interface FinancialTrends {
  workspace_id: string;
  target_name: string;
  years: string[];
  rows: TrendPoint[];
  revenue_cagr: number | null;
  generated_at: string;
}

export interface MacroPoint {
  date: string;
  value: number;
}

export interface MacroSeries {
  series_id: string;
  label: string;
  unit: string;
  note: string;
  latest_value: number;
  latest_date: string;
  yoy_change: number | null;
  points: MacroPoint[];
}

export interface MacroOverlay {
  workspace_id: string;
  target_name: string;
  sector: string;
  commentary: string;
  series: MacroSeries[];
  generated_at: string;
}

export interface AgencyShare {
  agency: string | null;
  amount: number;
  pct: number | null;
}

export interface GovConAward {
  award_id: string | null;
  recipient: string | null;
  agency: string | null;
  sub_agency: string | null;
  amount: number | null;
  description: string;
  pop_end: string | null;
  pop_start: string | null;
}

export interface RecompeteAward {
  award_id: string | null;
  agency: string | null;
  amount: number | null;
  pop_end: string | null;
}

export interface Recompete {
  count: number;
  value: number;
  awards: RecompeteAward[];
}

export interface GovConProfile {
  id: string;
  workspace_id: string;
  recipient_name: string;
  total_obligations: number;
  award_count: number;
  top_agency: string | null;
  top_agency_pct: number | null;
  agency_concentration: AgencyShare[];
  top_awards: GovConAward[];
  recompete: Recompete;
  created_at: string;
}

// --- Extensions: QoE/forensics, valuation/LBO, events, insiders, themes, news, filing watch ---

export type ForensicRating = "strong" | "neutral" | "weak" | "distress" | "elevated" | "n/a";

export interface ForensicComponent {
  name: string;
  value: number | null;
}
export interface ForensicScore {
  key: string; // "altman_z", "piotroski_f", "beneish_m", "accruals"
  label: string;
  value: number | null;
  rating: ForensicRating;
  interpretation: string;
  components: ForensicComponent[];
  available: boolean;
  note?: string;
}
export interface QoEMetric {
  key: string;
  label: string;
  unit: "pct" | "x" | "usd" | "days" | "ratio";
  value: number | null;
  commentary: string;
}
export interface Forensics {
  workspace_id: string;
  target_name: string;
  as_of_year: string | null;
  scores: ForensicScore[];
  qoe: QoEMetric[];
  notes: string[];
  generated_at: string;
}

export interface WACC {
  value: number | null;
  risk_free: number | null;
  equity_risk_premium: number;
  beta: number;
  cost_of_equity: number | null;
  cost_of_debt: number | null;
  tax_rate: number;
  debt_weight: number | null;
}
export interface DCF {
  fcf_base: number | null;
  growth: number;
  terminal_growth: number;
  wacc: number | null;
  enterprise_value: number | null;
  assumptions: string[];
}
export interface Valuation {
  workspace_id: string;
  target_name: string;
  ebitda: number | null;
  net_debt: number | null;
  wacc: WACC;
  dcf: DCF;
  notes: string[];
  generated_at: string;
}
export interface LboInputs {
  entry_multiple: number; // EV / EBITDA at entry
  exit_multiple: number;
  leverage: number; // entry net debt / EBITDA
  hold_years: number;
  ebitda_cagr: number; // decimal
}
export interface LboSensitivity {
  entry_multiples: number[];
  exit_multiples: number[];
  irr_grid: (number | null)[][];
  moic_grid: (number | null)[][];
}
export interface LboResult {
  entry_ev: number | null;
  entry_equity: number | null;
  exit_ev: number | null;
  exit_equity: number | null;
  irr: number | null;
  moic: number | null;
  inputs: LboInputs;
  sensitivity: LboSensitivity;
  assumptions: string[];
  generated_at: string;
}

export interface EventItem {
  code: string;
  label: string;
}
export interface FilingEvent {
  date: string;
  form: string;
  items: EventItem[];
  accession: string | null;
  url: string | null;
  significant: boolean;
}
export type ExternalSourceStatus = "available" | "partial" | "unavailable";
export interface EventTimeline {
  workspace_id: string;
  events: FilingEvent[];
  source_status: ExternalSourceStatus;
  source_error: string | null;
  generated_at: string;
}

export interface InsiderTx {
  date: string;
  insider: string;
  role: string;
  type: "buy" | "sell" | "other";
  shares: number | null;
  price: number | null;
  value: number | null;
  url: string | null;
}
export interface InsiderActivity {
  workspace_id: string;
  summary: { buys: number | null; sells: number | null; net_shares: number | null; window_days: number };
  transactions: InsiderTx[];
  source_status: ExternalSourceStatus;
  source_error: string | null;
  generated_at: string;
}

export interface ThemeHit {
  theme: string;
  label: string;
  count: number | null;
  hits: { form: string; date: string; url: string | null }[];
}
export interface ThemeScan {
  workspace_id: string;
  themes: ThemeHit[];
  source_status: ExternalSourceStatus;
  source_error: string | null;
  generated_at: string;
}

export interface NewsArticle {
  title: string;
  url: string;
  domain: string;
  seendate: string;
  sourcecountry?: string | null;
}
export interface NewsSignals {
  workspace_id: string;
  query: string;
  articles: NewsArticle[];
  source_status: ExternalSourceStatus;
  source_error: string | null;
  generated_at: string;
}

export interface FilingWatch {
  workspace_id: string;
  last_ingested_date: string | null;
  has_new: boolean | null;
  new_filings: { form: string; date: string; accession: string | null; url: string | null }[];
  source_status: ExternalSourceStatus;
  source_error: string | null;
  generated_at: string;
}

// --- Wave 3: private-company underwriting, model governance, and deal execution ---

export type MoneyValue = number | string;
export type CaseKey = "base" | "upside" | "downside";
export type SourceSnapshotStatus = "ready" | "partial" | "failed";
export type BridgeLayer = "management" | "sponsor" | "covenant";

export interface SourceSnapshot {
  id: string;
  workspace_id: string;
  target_id: string | null;
  source_kind: "financials" | "document" | "market_data" | "filing" | "user_input";
  source_type: string;
  source_name: string;
  version: number;
  supersedes_id: string | null;
  filename: string | null;
  content_type: string | null;
  storage_uri: string | null;
  input_hash: string;
  content_hash: string;
  byte_size: number | null;
  record_count: number;
  status: SourceSnapshotStatus;
  source_metadata: Record<string, unknown> | null;
  created_by: string;
  created_at: string;
  sealed_at: string;
}

export interface AccountMapping {
  id: string;
  workspace_id: string;
  source_type: string;
  raw_account: string;
  raw_account_normalized: string;
  canonical_account: string;
  statement: "income_statement" | "balance_sheet" | "cash_flow" | "kpi";
  sign_multiplier: MoneyValue;
  status: "draft" | "approved" | "rejected";
  version: number;
  supersedes_id: string | null;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface CanonicalFinancialFact {
  id: string;
  workspace_id: string;
  target_id: string;
  source_snapshot_id: string;
  account_mapping_id: string | null;
  statement: string;
  raw_account: string;
  raw_account_normalized: string;
  canonical_account: string | null;
  mapping_state: string;
  period_start: string | null;
  period_end: string;
  period_type: string;
  raw_value: MoneyValue;
  scale_factor: MoneyValue;
  value: MoneyValue;
  unit: string;
  currency: string | null;
  source_sheet: string | null;
  source_row: number | null;
  source_locator: string;
  provenance: Record<string, unknown> | null;
  row_hash: string;
  created_at: string;
}

export interface FinancialReconciliation {
  id: string;
  workspace_id: string;
  source_snapshot_id: string;
  period_end: string;
  assets: MoneyValue | null;
  liabilities_and_equity: MoneyValue | null;
  difference: MoneyValue | null;
  tolerance: MoneyValue | null;
  status: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface FinancialImportException {
  id: string;
  workspace_id: string;
  source_snapshot_id: string;
  fact_id: string | null;
  code: string;
  severity: string;
  state: string;
  message: string;
  details: Record<string, unknown> | null;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinancialImportResult {
  snapshot: SourceSnapshot;
  row_count: number;
  mapped_count: number;
  unmapped_count: number;
  open_exception_count: number;
  reconciliations: FinancialReconciliation[];
}

export interface QoEAdjustment {
  id: string;
  workspace_id: string;
  target_id: string;
  source_snapshot_id: string | null;
  period_start: string | null;
  period_end: string;
  bridge_layer: BridgeLayer;
  title: string;
  description: string;
  category: string;
  amount: MoneyValue;
  currency: string;
  is_recurring: boolean;
  is_run_rate: boolean;
  is_cash: boolean;
  owner: string;
  evidence_ref: string | null;
  source_locator: string | null;
  status: string;
  created_by: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string;
  created_at: string;
  updated_at: string;
}

export interface QoEBridge {
  workspace_id: string;
  target_id: string;
  period_end: string | null;
  currency: string | null;
  status: "ready" | "incomplete";
  reported_ebitda: MoneyValue | null;
  management_adjustments: MoneyValue;
  management_ebitda: MoneyValue | null;
  sponsor_adjustments: MoneyValue;
  sponsor_ebitda: MoneyValue | null;
  covenant_adjustments: MoneyValue;
  covenant_ebitda: MoneyValue | null;
  included_adjustment_ids: string[];
  excluded_adjustment_count: number;
  source_snapshot_id: string | null;
  source_locator: string | null;
  derivation: Record<string, unknown> | null;
  warnings: string[];
}

export interface OperatingDrivers {
  annual_revenue_growth: number;
  gross_margin: number;
  ebitda_margin: number;
  da_percent_revenue: number;
  capex_percent_revenue: number;
  net_working_capital_percent_revenue: number;
  cash_tax_rate: number;
  base_rate: number;
}

export interface OperatingPeriodAssumption {
  label: string;
  months: number;
  annual_revenue_growth?: number | null;
  gross_margin?: number | null;
  ebitda_margin?: number | null;
  da_percent_revenue?: number | null;
  capex_percent_revenue?: number | null;
  net_working_capital_percent_revenue?: number | null;
  cash_tax_rate?: number | null;
  base_rate?: number | null;
}

export interface DebtTrancheAssumption {
  name: string;
  tranche_type: "revolver" | "term_loan" | "second_lien" | "mezzanine" | "seller_note";
  initial_amount: number;
  commitment?: number | null;
  senior: boolean;
  spread: number;
  base_rate_floor: number;
  pik_rate: number;
  annual_amortization_rate: number;
  cash_sweep_priority: number;
  sweep_eligible: boolean;
  maturity_period?: string | null;
  oid_discount: number;
  financing_fee_percent: number;
}

export interface CovenantAssumption {
  name: string;
  metric: "total_leverage" | "senior_leverage" | "interest_coverage" | "fixed_charge_coverage" | "minimum_liquidity";
  test: "maximum" | "minimum";
  threshold: number;
  threshold_by_period: Record<string, number>;
}

export interface UnderwritingAssumptions {
  currency: string;
  historical: {
    ltm_revenue: number;
    ltm_ebitda: number;
    starting_cash: number;
    starting_net_working_capital: number;
    existing_debt: number;
  };
  transaction: {
    close_date: string;
    entry_multiple: number;
    exit_multiple: number;
    hold_period_years: number;
    transaction_fees: number;
    management_options_cashout: number;
    other_uses: number;
    seller_rollover: number;
    minimum_cash: number;
    cash_sweep_percent: number;
  };
  projection: {
    default_drivers: OperatingDrivers;
    periods: OperatingPeriodAssumption[];
  };
  debt_tranches: DebtTrancheAssumption[];
  covenants: CovenantAssumption[];
  valuation: {
    discount_rate: number;
    terminal_growth_rate: number;
    mid_year_convention: boolean;
  };
}

export interface SourceUseLine { name: string; amount: number }
export interface DebtTranchePeriodResult {
  name: string;
  tranche_type: DebtTrancheAssumption["tranche_type"];
  opening_balance: number;
  cash_rate: number;
  cash_interest: number;
  pik_interest: number;
  required_amortization: number;
  paid_amortization: number;
  revolver_draw: number;
  cash_sweep: number;
  unpaid_amortization: number;
  ending_balance: number;
}
export interface CovenantPeriodResult {
  name: string;
  metric: CovenantAssumption["metric"];
  test: "maximum" | "minimum";
  actual: number | null;
  threshold: number;
  headroom: number | null;
  passed: boolean | null;
}
export interface ProjectionPeriodResult {
  label: string;
  start_date: string;
  end_date: string;
  months: number;
  year_fraction: number;
  revenue: number;
  annualized_revenue: number;
  revenue_growth: number;
  gross_profit: number;
  ebitda: number;
  ebitda_margin: number;
  cash_interest: number;
  pik_interest: number;
  cash_taxes: number;
  net_income: number;
  change_in_net_working_capital: number;
  capex: number;
  fcff: number;
  ending_cash: number;
  liquidity_shortfall: number;
  total_debt: number;
  net_debt: number;
  total_leverage: number | null;
  senior_leverage: number | null;
  interest_coverage: number | null;
  fixed_charge_coverage: number | null;
  liquidity: number;
  debt_tranches: DebtTranchePeriodResult[];
  covenants: CovenantPeriodResult[];
  [key: string]: unknown;
}
export interface UnderwritingResult {
  currency: string;
  sources_uses: {
    entry_enterprise_value: number;
    equity_purchase_price: number;
    uses: SourceUseLine[];
    sources: SourceUseLine[];
    total_uses: number;
    total_sources: number;
    sponsor_equity: number;
    rollover_equity: number;
    sponsor_ownership: number;
    balanced: boolean;
  };
  projection: ProjectionPeriodResult[];
  dcf: {
    discount_rate: number;
    terminal_growth_rate: number;
    pv_explicit_fcff: number;
    terminal_value: number;
    pv_terminal_value: number;
    enterprise_value: number;
    net_debt: number;
    equity_value: number;
    terminal_value_percent: number | null;
  };
  returns: {
    exit_enterprise_value: number;
    exit_debt: number;
    exit_cash: number;
    exit_equity_value: number;
    sponsor_exit_proceeds: number;
    sponsor_invested_capital: number;
    moic: number | null;
    xirr: number | null;
    cash_flows: Record<string, unknown>[];
  };
  summary: {
    revenue_cagr: number | null;
    exit_ebitda: number;
    exit_ebitda_margin: number;
    minimum_liquidity: number;
    maximum_total_leverage: number | null;
    first_covenant_breach: string | null;
    first_debt_service_default: string | null;
  };
  generated_at: string;
}
export interface UnderwritingDecision {
  id: string;
  decision: string;
  actor: string;
  rationale: string;
  created_at: string;
}
export interface UnderwritingCaseVersion {
  id: string;
  workspace_id: string;
  case_key: CaseKey;
  label: string;
  version: number;
  parent_version_id: string | null;
  schema_version: string;
  assumptions: UnderwritingAssumptions;
  result: UnderwritingResult;
  input_hash: string;
  output_hash: string;
  created_by: string;
  change_note: string;
  created_at: string;
  latest_decision: UnderwritingDecision | null;
}

export type SensitivityVariable = "entry_multiple" | "exit_multiple" | "base_rate_shift" | "revenue_growth_shift" | "ebitda_margin_shift";
export interface SensitivityResult {
  row_variable: SensitivityVariable;
  row_values: number[];
  column_variable: SensitivityVariable;
  column_values: number[];
  metric: "irr" | "moic" | "minimum_liquidity";
  grid: (number | null)[][];
}
export interface ReverseStressResult {
  status: "solved" | "no_solution";
  variable: SensitivityVariable;
  objective: "irr" | "moic" | "minimum_liquidity";
  target: number;
  solved_value: number | null;
  achieved_value: number | null;
  lower_value: number | null;
  upper_value: number | null;
  iterations: number;
}
export interface WorkingCapitalObservation {
  observation_date: string;
  accounts_receivable: number;
  inventory: number;
  other_operating_current_assets: number;
  accounts_payable: number;
  accrued_liabilities: number;
  deferred_revenue: number;
  other_operating_current_liabilities: number;
  excluded_net_amount: number;
}
export interface WorkingCapitalPegResult {
  method: string;
  peg: number;
  trailing_average: number;
  trailing_median: number;
  low: number;
  high: number;
  seasonal_month: number;
  seasonal_average: number | null;
  delivered_working_capital: number | null;
  purchase_price_adjustment: number | null;
  observations: { observation_date: string; normalized_working_capital: number }[];
}
export interface ValuationReference { name: string; ev_ebitda_multiple: number; source: string; as_of_date?: string | null; evidence_ref?: string | null }
export interface ValuationTriangulationResult {
  ebitda: number;
  net_debt: number;
  methods: { method: "dcf" | "public_comps" | "precedent_transactions"; reference_count: number; multiple_low: number | null; multiple_median: number | null; multiple_high: number | null; enterprise_value_low: number; enterprise_value_median: number; enterprise_value_high: number; requested_weight: number; normalized_weight: number }[];
  blended_enterprise_value: number;
  blended_equity_value: number;
  valuation_low: number;
  valuation_high: number;
  warnings: string[];
}

export type DealStage = "sourcing" | "screening" | "initial_review" | "diligence" | "ic_review" | "signing" | "closed" | "declined";
export interface Organization { id: string; name: string; slug: string; status: string; created_at: string; updated_at: string; external_tenant_id: string | null; identity_provider: Record<string, unknown> | null }
export interface Fund { id: string; organization_id: string; name: string; vintage_year: number | null; base_currency: string; strategy: string; status: string; created_at: string; updated_at: string }
export interface Deal {
  id: string; organization_id: string; fund_id: string; workspace_id: string | null; code: string; name: string; target_company: string; deal_type: string; stage: DealStage; status: string; owner_actor_id: string | null; ic_date: string | null; summary: string; version: number; created_at: string; updated_at: string;
}
export interface StageGate { id: string; deal_id: string; stage: string; code: string; label: string; required: boolean; status: string; evidence_refs: string[]; resolution_note: string; resolved_by_actor_id: string | null; resolved_at: string | null; created_at: string; updated_at: string }
export interface TeamMember { id: string; deal_id: string; actor_id: string; display_name: string; email: string | null; role: string; is_active: boolean; added_by_actor_id: string | null; created_at: string; updated_at: string }
export interface DealWorkstream { id: string; deal_id: string; slug: string; label: string; description: string; status: string; lead_actor_id: string | null; due_date: string | null; created_at: string; updated_at: string }
export interface DealMilestone { id: string; deal_id: string; workstream_id: string | null; title: string; description: string; status: string; due_date: string | null; owner_actor_id: string | null; completed_at: string | null; completed_by_actor_id: string | null; created_at: string; updated_at: string }
export interface DealTask { id: string; deal_id: string; workstream_id: string | null; milestone_id: string | null; parent_task_id: string | null; title: string; description: string; status: string; priority: string; assignee_actor_id: string | null; due_date: string | null; dependency_task_ids: string[]; blocked_reason: string; completed_at: string | null; completed_by_actor_id: string | null; created_at: string; updated_at: string }
export interface DiligenceRequestRecord { id: string; deal_id: string; workstream_id: string | null; request_number: number; title: string; question: string; rationale: string; status: string; priority: string; owner_actor_id: string | null; respondent_actor_id: string | null; due_date: string | null; requested_at: string | null; last_response_at: string | null; accepted_at: string | null; accepted_by_actor_id: string | null; review_note: string; created_at: string; updated_at: string }
export interface LedgerEntry { id: string; deal_id: string; root_entry_id: string | null; supersedes_entry_id: string | null; version: number; entry_type: "thesis" | "issue" | "risk" | "decision"; title: string; description: string; status: string; severity: string; owner_actor_id: string | null; evidence_refs: string[]; related_artifact_ids: string[]; created_by_actor_id: string | null; created_at: string; updated_at: string }
export interface ICPacket { id: string; deal_id: string; version: number; previous_packet_id: string | null; title: string; status: string; scenario_snapshot: Record<string, unknown>; model_snapshot: Record<string, unknown>; evidence_manifest: Record<string, unknown>[]; thesis_snapshot: Record<string, unknown>[]; risk_snapshot: Record<string, unknown>[]; decision_request: Record<string, unknown>; readiness_snapshot: Record<string, unknown>; ready_for_submission: boolean; content_hash: string; created_by_actor_id: string | null; submitted_by_actor_id: string | null; submitted_at: string | null; frozen_at: string | null; created_at: string; updated_at: string }
export interface GovernedICPacketCreate {
  title: string;
  assembly_mode: "governed";
  case_version_ids: string[];
  approved_claim_ids: string[];
  workspace_evidence_refs: string[];
  decision_request: Record<string, unknown>;
  previous_packet_id?: string | null;
}
export interface ReadinessResult { packet_id: string; checked_at: string; ready: boolean; checks: { code: string; passed: boolean; message: string; blocking_count: number; entity_ids: string[] }[] }
export interface ICComment { id: string; packet_id: string; parent_comment_id: string | null; section_path: string; body: string; blocking: boolean; status: string; author_actor_id: string | null; resolution: string; resolved_by_actor_id: string | null; resolved_at: string | null; created_at: string; updated_at: string }
export interface ICCondition { id: string; deal_id: string; packet_id: string; decision_id: string; description: string; owner_actor_id: string | null; due_date: string | null; status: string; evidence_refs: string[]; resolution_note: string; resolved_by_actor_id: string | null; resolved_at: string | null; created_at: string; updated_at: string }
export interface ExportManifest { id: string; packet_id: string; format: "pdf" | "docx" | "xlsx" | "json"; manifest: Record<string, unknown>; manifest_hash: string; requested_by_actor_id: string | null; created_at: string }
export interface WorkflowAuditEvent { id: string; organization_id: string; deal_id: string | null; actor_id: string | null; actor_display_name: string | null; action: string; entity_type: string; entity_id: string; detail: Record<string, unknown>; request_id: string | null; created_at: string }

export interface WorkflowActor {
  actorId?: string;
  actorName?: string;
  organizationId?: string;
  roles?: string[];
}

export type MembershipRole = "owner" | "admin" | "member" | "viewer";

export interface AuthPrincipal {
  user_id: string;
  session_id: string;
  email: string;
  display_name: string;
  organization_id: string;
  membership_id: string;
  role: MembershipRole;
}

export interface OrganizationMembership {
  id: string;
  user_id: string;
  organization_id: string;
  role: MembershipRole;
  status: "active" | "suspended";
  created_at: string;
  updated_at: string;
  email: string | null;
  display_name: string | null;
}

export interface AuthSessionToken {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  principal: AuthPrincipal;
  memberships: OrganizationMembership[];
}

export type BrowserAuthSession = Omit<AuthSessionToken, "access_token">;

export interface CurrentIdentity {
  principal: AuthPrincipal;
  memberships: OrganizationMembership[];
}

export interface LoginInput {
  email: string;
  password: string;
  organization_id?: string;
}

export interface RegistrationInput {
  email: string;
  display_name: string;
  password: string;
  organization_name: string;
  organization_slug: string;
}

export interface WorkspaceGovernancePatch {
  data_classification?: WorkspaceDataClassification;
  external_llm_allowed?: boolean;
}

export interface DataRoomDocument { id: string; deal_id: string; logical_document_id: string; version: number; supersedes_document_id: string | null; title: string; filename: string; original_filename: string; extension: string; content_type: string; sha256: string; byte_size: number; document_metadata: Record<string, unknown>; source_kind: string; uploaded_by_actor_id: string | null; created_at: string }
export interface IntelligenceCitation { document_id: string; logical_document_id: string; document_version: number; filename: string; sha256: string; chunk_id: string; content_hash: string; locator: Record<string, unknown>; quote: string }
export interface CitedQARun { id: string; deal_id: string; question: string; filters: Record<string, unknown>; status: "answered" | "abstained"; answer: string; citations: IntelligenceCitation[]; retrieval_metadata: Record<string, unknown>; answer_hash: string; algorithm_version: string; created_by_actor_id: string | null; created_at: string }
export type ClaimCategory = "debt_term" | "customer" | "contract" | "kpi" | "qoe_candidate";
export interface StructuredClaim { id: string; deal_id: string; logical_claim_id: string; revision: number; supersedes_claim_id: string | null; document_id: string; chunk_id: string; category: ClaimCategory; field_name: string; value_text: string; value_number: number | null; unit: string | null; period: string | null; currency: string | null; confidence: number; source_locator: Record<string, unknown>; source_span: Record<string, unknown>; review_status: "unreviewed" | "approved" | "rejected"; extraction_version: string; created_by_actor_id: string | null; created_at: string }
export interface ClaimCollection { approved: StructuredClaim[]; pending: StructuredClaim[]; rejected: StructuredClaim[]; counts: Record<string, number> }
export interface ComparisonFinding { finding_type: string; summary: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null; shared_terms: string[] }
export interface DocumentComparison { id: string; deal_id: string; from_document_id: string; to_document_id: string; comparison_type: "change" | "contradiction"; findings: ComparisonFinding[]; finding_count: number; algorithm_version: string; created_by_actor_id: string | null; created_at: string }
export interface IntelligenceEvaluation { id: string; deal_id: string; cases: Record<string, unknown>[]; qa_run_ids: string[]; metrics: Record<string, unknown>; passed: boolean; algorithm_version: string; created_by_actor_id: string | null; created_at: string }

// --- Portfolio command center -------------------------------------------------

export interface PortfolioDistributionPoint {
  key: string;
  label: string;
  count: number;
  percent: number;
}

export interface PortfolioHeadline {
  deals: number;
  active_deals: number;
  funds: number;
  at_ic: number;
  ic_next_30_days: number;
  overdue_tasks: number;
  critical_risks: number;
  open_conditions: number;
  average_readiness: number;
}

export interface PortfolioReadinessComponent {
  key: string;
  label: string;
  score: number;
  weight: number;
  passed: number;
  total: number;
  explanation: string;
}

export interface PortfolioSourceHealth {
  status: "ready" | "partial" | "failed" | "not_configured" | string;
  total_sources: number;
  ready: number;
  partial: number;
  failed: number;
  freshest_at: string | null;
  oldest_age_days: number | null;
  stale: boolean;
}

export interface PortfolioFinancialQuality {
  mapping_coverage: number | null;
  mapped_facts: number;
  total_facts: number;
  reconciliation_score: number | null;
  reconciliations_passed: number;
  reconciliations_total: number;
  open_exceptions: number;
  qoe_adjustment_amount: number;
  qoe_materiality: number | null;
  reported_ebitda: number | null;
  sponsor_adjusted_ebitda: number | null;
  ebitda_variance: number | null;
  period_consistent: boolean | null;
  period_diagnostics: string[];
}

export interface PortfolioDealRow {
  id: string;
  code: string;
  name: string;
  target_company: string;
  fund_id: string;
  fund_name: string;
  strategy: string;
  workspace_id: string | null;
  sector: string;
  stage: DealStage;
  status: string;
  owner_actor_id: string | null;
  ic_date: string | null;
  stage_age_days: number;
  readiness_score: number;
  readiness_components: PortfolioReadinessComponent[];
  source_health: PortfolioSourceHealth;
  financial_quality: PortfolioFinancialQuality;
}

export interface PortfolioCalendarItem {
  deal_id: string;
  code: string;
  name: string;
  ic_date: string;
  days_until: number;
  stage: DealStage;
}

export interface PortfolioTaskQueueItem {
  task_id: string;
  deal_id: string;
  deal_code: string;
  title: string;
  assignee_actor_id: string | null;
  priority: string;
  status: string;
  due_date: string;
  days_overdue: number;
}

export interface PortfolioWorkstreamHealth {
  deal_id: string;
  deal_code: string;
  total: number;
  complete: number;
  in_progress: number;
  blocked: number;
  late: number;
  health: string;
}

export interface PortfolioDiligenceSLAItem {
  request_id: string;
  deal_id: string;
  deal_code: string;
  request_number: number;
  title: string;
  status: string;
  priority: string;
  owner_actor_id: string | null;
  due_date: string | null;
  age_days: number;
  days_overdue: number;
  sla_status: "overdue" | "due_soon" | "on_track" | string;
}

export interface PortfolioRiskRegisterItem {
  entry_id: string;
  deal_id: string;
  deal_code: string;
  title: string;
  severity: string;
  status: string;
  owner_actor_id: string | null;
  evidence_refs: string[];
  age_days: number;
}

export interface PortfolioConditionTrackerItem {
  condition_id: string;
  deal_id: string;
  deal_code: string;
  description: string;
  owner_actor_id: string | null;
  due_date: string | null;
  status: string;
  days_overdue: number;
}

export interface PortfolioWorkloadItem {
  actor_id: string;
  open_tasks: number;
  overdue_tasks: number;
  critical_tasks: number;
  deals: number;
}

export interface PortfolioReturnCase {
  case_key: string;
  case_version_id: string;
  version: number;
  created_at: string;
  moic: number | null;
  xirr: number | null;
  minimum_liquidity: number | null;
  first_covenant_breach: string | null;
  first_debt_service_default: string | null;
}

export interface PortfolioDealReturnsSnapshot {
  deal_id: string;
  deal_code: string;
  cases: PortfolioReturnCase[];
}

export interface PortfolioWatchlistItem {
  deal_id: string;
  deal_code: string;
  case_key: string;
  reason: string;
  severity: string;
  metric: string;
  value: number | string | null;
}

export interface PortfolioImportExceptionItem {
  exception_id: string;
  deal_id: string;
  deal_code: string;
  workspace_id: string;
  severity: string;
  code: string;
  message: string;
  state: string;
  age_days: number;
}

export interface PortfolioFilters {
  search: string | null;
  stage: string | null;
  fund_id: string | null;
  as_of: string;
  ic_window_days: number;
}

export interface PortfolioDashboard {
  organization_id: string;
  generated_at: string;
  filters: PortfolioFilters;
  headline: PortfolioHeadline;
  stage_funnel: PortfolioDistributionPoint[];
  sector_exposure: PortfolioDistributionPoint[];
  strategy_exposure: PortfolioDistributionPoint[];
  deals: PortfolioDealRow[];
  upcoming_ic: PortfolioCalendarItem[];
  overdue_tasks: PortfolioTaskQueueItem[];
  workstream_health: PortfolioWorkstreamHealth[];
  diligence_sla: PortfolioDiligenceSLAItem[];
  critical_risks: PortfolioRiskRegisterItem[];
  conditions_to_close: PortfolioConditionTrackerItem[];
  team_workload: PortfolioWorkloadItem[];
  returns_snapshots: PortfolioDealReturnsSnapshot[];
  downside_watchlist: PortfolioWatchlistItem[];
  covenant_watchlist: PortfolioWatchlistItem[];
  import_exceptions: PortfolioImportExceptionItem[];
}

export interface PortfolioQuery {
  search?: string;
  stage?: DealStage;
  fundId?: string;
  asOf?: string;
  icWindowDays?: number;
}

// --- Wave 4 additions -------------------------------------------------------

export interface InsiderCluster {
  direction: "buy" | "sell";
  start: string;
  end: string;
  participants: number;
  transactions: number;
  total_shares: number;
  total_value: number | null;
}

export interface InsiderPatterns {
  workspace_id: string;
  clusters: InsiderCluster[];
  plan_summary: { planned: number; discretionary: number; unknown: number };
  role_split: Record<string, { buys: number; sells: number }>;
  source_status: ExternalSourceStatus;
  source_error: string | null;
  generated_at: string;
}

export interface CovenantHeadroomPeriod {
  period: string;
  metric_value: number | null;
  threshold: number | null;
  headroom: number | null;
  breached: boolean;
}
export interface CovenantHeadroomResult {
  covenants: { name: string; periods: CovenantHeadroomPeriod[]; first_breach_period: string | null }[];
}

export interface CaseVarianceLine {
  key: string;
  label: string;
  management: MoneyValue | null;
  sponsor: MoneyValue | null;
  absolute_delta: MoneyValue | null;
  pct_delta: number | null;
  materiality_rank: number;
}
export interface CaseVarianceResult {
  lines: CaseVarianceLine[];
}

export interface ExitReadinessDimension {
  key: string;
  label: string;
  threshold: string;
  direction: string;
  score: number;
  rating: string;
}
export interface ExitReadinessResult {
  dimensions: ExitReadinessDimension[];
  hold_period_grid: { hold_years: number; irr: number | null; moic: number | null }[];
}

export interface FootballFieldMethod {
  method: string;
  low: MoneyValue | null;
  mid: MoneyValue | null;
  high: MoneyValue | null;
  weight: number;
  included: boolean;
  excluded_reason: string | null;
}
export interface FootballFieldResult {
  methods: FootballFieldMethod[];
}

export interface NotificationItem {
  id: string;
  organization_id: string;
  actor_id: string | null;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  title: string;
  body: string;
  read_at: string | null;
  created_at: string;
}

export interface SegmentSeriesPoint { period_end: string; revenue: number | null; }
export interface SegmentRevenue {
  workspace_id: string;
  segments: { segment_name: string; source_concept: string; periods: SegmentSeriesPoint[] }[];
  source_status: ExternalSourceStatus;
  note: string | null;
  generated_at: string;
}

export interface InstitutionalOwnership {
  workspace_id: string;
  scope: string;
  concentration: { hhi: number | null; top5_share: number | null; holder_count: number | null; total_value: number | null };
  holdings: { name: string; value: number | null; weight: number | null }[];
  source_status: ExternalSourceStatus;
  note: string | null;
  generated_at: string;
}

export interface ActivistStakeEvent {
  type: "13D" | "13G";
  filer: string | null;
  filing_date: string;
  percent_owned: number | null;
  is_activist: boolean;
  is_amendment?: boolean;
}
export interface ActivistStakes {
  workspace_id: string;
  events: ActivistStakeEvent[];
  source_status: ExternalSourceStatus;
  note: string | null;
  generated_at: string;
}

export interface DebtMaturityRow { bucket: string; amount: number | null; source_concept: string; }
export interface DebtMaturitySchedule {
  workspace_id: string;
  schedule: DebtMaturityRow[];
  total_scheduled: number | null;
  missing_buckets: string[];
  source_status: ExternalSourceStatus;
  source_note: string | null;
  generated_at: string;
}

export interface GovernanceRedFlag { flag: string; label: string; present: boolean; evidence: string | null; }
export interface ExecCompRow {
  name: string; title: string | null;
  salary: number | null; bonus: number | null; stock_awards: number | null; total: number | null;
}
export interface GovernanceProfile {
  workspace_id: string;
  def14a_accession: string | null;
  filing_date: string | null;
  exec_comp: ExecCompRow[];
  red_flags: GovernanceRedFlag[];
  source_status: ExternalSourceStatus;
  raw_note: string | null;
}

export interface CompSimilarityRow { ticker: string; company_name: string; similarity?: number; in_sic_set?: boolean; in_embedding_top?: boolean; }
export interface CompSimilarity {
  workspace_id: string;
  target_name: string;
  target_description: string;
  available: boolean;
  embedding_ranked: CompSimilarityRow[];
  sic_ranked: CompSimilarityRow[];
  disagreements: { embedding_only: CompSimilarityRow[]; sic_only: CompSimilarityRow[] };
  note: string | null;
  generated_at: string;
}

export interface RiskDiffCitation {
  filing_id: string; form_type: string | null; filing_date: string | null;
  section: string; document_url: string | null; chunk_index: number; quote: string;
}
export interface RiskDiff {
  workspace_id: string;
  source_status: string;
  note: string;
  older_filing: { filing_id: string; form_type: string; filing_date: string; document_url: string | null } | null;
  newer_filing: { filing_id: string; form_type: string; filing_date: string; document_url: string | null } | null;
  added: RiskDiffCitation[];
  removed: RiskDiffCitation[];
  changed: { old: RiskDiffCitation; new: RiskDiffCitation; similarity: number }[];
  method: string;
  generated_at: string;
}

export interface CrossCorpusCitation {
  corpus: "public_filing" | "confidential_dataroom";
  confidential: boolean;
  label: string;
  quote: string;
  source_name: string;
  provenance: Record<string, unknown>;
}
export interface CrossCorpusQA {
  workspace_id: string;
  deal_id: string | null;
  question: string;
  status: "answered" | "partial" | "abstained";
  answer: string;
  citations: CrossCorpusCitation[];
  corpora: Record<string, unknown>;
  retrieval: Record<string, unknown>;
  method: string;
  generated_at: string;
}

export interface WorkspaceSearchHit {
  artifact_type: string; artifact_id: string; title: string; snippet: string; rank: number;
}
export interface WorkspaceSearchResult {
  query: string; hits: WorkspaceSearchHit[]; engine: string; total: number;
}

export interface QuotaBucket { name: string; used: number; limit: number; window_seconds: number; remaining: number | null; }
export interface QuotaUsage { organization_id: string; buckets: QuotaBucket[]; }

export interface SignalsSection {
  kind: string;
  source_status: ExternalSourceStatus;
  source_error: string | null;
  summary: string;
  items: Record<string, unknown>[];
}
export interface SignalsOverview {
  workspace_id: string;
  sections: SignalsSection[];
  overall_status: ExternalSourceStatus;
  generated_at: string;
}

export interface FundConstructionEntry {
  fund_id: string; name: string; vintage_year: number | null;
  deployed: number | null; target: number | null;
  pacing: { expected_pct: number | null; actual_pct: number | null; status: string };
  exposures: Record<string, { key: string; label: string; exposure_pct: number }[]>;
  concentration_breaches: { dimension: string; key: string; exposure_pct: number; limit: number; excess: number }[];
  sizing_coverage: { total_deals: number; sized_deals: number; coverage_pct: number };
}
export interface FundConstruction { organization_id: string; funds: FundConstructionEntry[]; generated_at: string; }

export interface WatchlistEntry {
  id: string; organization_id: string; ticker: string | null; cik: string | null;
  company_name: string | null; last_seen_accession: string | null; last_checked_at: string | null; active: boolean;
}
