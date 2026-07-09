// DealLens shared types — mirror of docs/CONTRACTS.md. Keep in sync with the backend schemas.

export type DealType =
  | "buyout"
  | "growth_equity"
  | "private_credit"
  | "public_equity"
  | "govcon"
  | "software_platform";

export type WorkspaceStatus = "draft" | "in_progress" | "complete";
export type TargetType = "public_company" | "synthetic_private";
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
  target_id: string | null;
  deal_type: DealType;
  investment_question: string;
  status: WorkspaceStatus;
  created_at: string;
  updated_at: string;
}

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

export interface HealthStatus {
  status: string;
  llm_mode: string;
  database: string;
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
export interface EventTimeline {
  workspace_id: string;
  events: FilingEvent[];
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
  summary: { buys: number; sells: number; net_shares: number | null; window_days: number };
  transactions: InsiderTx[];
  generated_at: string;
}

export interface ThemeHit {
  theme: string;
  label: string;
  count: number;
  hits: { form: string; date: string; url: string | null }[];
}
export interface ThemeScan {
  workspace_id: string;
  themes: ThemeHit[];
  generated_at: string;
}

export interface NewsArticle {
  title: string;
  url: string;
  domain: string;
  seendate: string;
  sourcecountry?: string;
}
export interface NewsSignals {
  workspace_id: string;
  query: string;
  articles: NewsArticle[];
  generated_at: string;
}

export interface FilingWatch {
  workspace_id: string;
  last_ingested_date: string | null;
  has_new: boolean;
  new_filings: { form: string; date: string; accession: string | null; url: string | null }[];
  generated_at: string;
}
