// DealLens API client. Thin typed wrapper over fetch against the FastAPI backend.
// Server Components call these directly; Client Components use the same functions.

import type {
  Workspace,
  WorkspaceOverview,
  Target,
  Filing,
  ComparableCompany,
  Evidence,
  RiskFinding,
  DiligenceQuestion,
  DiligencePlan,
  FinancialBenchmark,
  FinancialTrends,
  MacroOverlay,
  GovConProfile,
  Forensics,
  Valuation,
  LboInputs,
  LboResult,
  EventTimeline,
  InsiderActivity,
  ThemeScan,
  NewsSignals,
  FilingWatch,
  Memo,
  RedTeam,
  SecSearchResult,
  HealthStatus,
  DealType,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

type FetchOpts = { method?: string; body?: unknown; cache?: RequestCache };

async function request<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { method = "GET", body, cache = "no-store" } = opts;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache,
    });
  } catch (e) {
    throw new ApiError(0, `Cannot reach API at ${API_BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = (data && (data.detail || data.message)) || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Return null on 404 instead of throwing — for "not generated yet" artifacts.
async function requestOrNull<T>(path: string, opts: FetchOpts = {}): Promise<T | null> {
  try {
    return await request<T>(path, opts);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export const api = {
  health: () => request<HealthStatus>("/api/health"),

  listWorkspaces: () => request<Workspace[]>("/api/workspaces"),
  createWorkspace: (body: {
    ticker?: string;
    name?: string;
    deal_type: DealType;
    investment_question?: string;
  }) => request<Workspace>("/api/workspaces", { method: "POST", body }),
  getWorkspace: (id: string) => request<WorkspaceOverview>(`/api/workspaces/${id}`),

  getTarget: (id: string) => requestOrNull<Target>(`/api/workspaces/${id}/target`),
  setTarget: (id: string, body: Partial<Target>) =>
    request<Target>(`/api/workspaces/${id}/target`, { method: "POST", body }),

  secSearch: (q: string) =>
    request<SecSearchResult[]>(`/api/sec/search?q=${encodeURIComponent(q)}`),
  secIngest: (body: { workspace_id: string; ticker?: string; cik?: string; limit?: number }) =>
    request<Filing[]>("/api/sec/ingest", { method: "POST", body }),
  getFilings: (id: string) => request<Filing[]>(`/api/workspaces/${id}/filings`),

  getComps: (id: string) => request<ComparableCompany[]>(`/api/workspaces/${id}/comps`),
  addComps: (id: string, tickers: string[]) =>
    request<ComparableCompany[]>(`/api/workspaces/${id}/comps`, {
      method: "POST",
      body: { tickers },
    }),
  getBenchmark: (id: string) =>
    requestOrNull<FinancialBenchmark>(`/api/workspaces/${id}/benchmark`),

  getPlan: (id: string) => requestOrNull<DiligencePlan>(`/api/workspaces/${id}/plan`),
  generatePlan: (id: string) =>
    request<DiligencePlan>(`/api/workspaces/${id}/plan/generate`, { method: "POST" }),

  getRisks: (id: string) => request<RiskFinding[]>(`/api/workspaces/${id}/risks`),
  generateRisks: (id: string) =>
    request<RiskFinding[]>(`/api/workspaces/${id}/risks/generate`, { method: "POST" }),

  getQuestions: (id: string) => request<DiligenceQuestion[]>(`/api/workspaces/${id}/questions`),
  generateQuestions: (id: string) =>
    request<DiligenceQuestion[]>(`/api/workspaces/${id}/questions/generate`, { method: "POST" }),

  getMemo: (id: string) => requestOrNull<Memo>(`/api/workspaces/${id}/memo`),
  generateMemo: (id: string) =>
    request<Memo>(`/api/workspaces/${id}/memo/generate`, { method: "POST" }),

  getRedTeam: (id: string) => requestOrNull<RedTeam>(`/api/workspaces/${id}/red-team`),
  generateRedTeam: (id: string) =>
    request<RedTeam>(`/api/workspaces/${id}/red-team/generate`, { method: "POST" }),

  getEvidence: (id: string) => request<Evidence[]>(`/api/workspaces/${id}/evidence`),

  // Roadmap extensions
  getTrends: (id: string) => requestOrNull<FinancialTrends>(`/api/workspaces/${id}/trends`),
  getMacro: (id: string) => requestOrNull<MacroOverlay>(`/api/workspaces/${id}/macro`),
  getGovCon: (id: string) => requestOrNull<GovConProfile>(`/api/workspaces/${id}/govcon`),
  generateGovCon: (id: string, recipient_name?: string) =>
    request<GovConProfile>(`/api/workspaces/${id}/govcon`, {
      method: "POST",
      body: { recipient_name: recipient_name || undefined },
    }),

  // QoE / forensics
  getForensics: (id: string) => requestOrNull<Forensics>(`/api/workspaces/${id}/forensics`),

  // Valuation / LBO
  getValuation: (id: string) => requestOrNull<Valuation>(`/api/workspaces/${id}/valuation`),
  runLbo: (id: string, inputs: LboInputs) =>
    request<LboResult>(`/api/workspaces/${id}/lbo`, { method: "POST", body: inputs }),

  // SEC event / insider / theme feeds
  getEvents: (id: string) => requestOrNull<EventTimeline>(`/api/workspaces/${id}/events`),
  getInsiders: (id: string) => requestOrNull<InsiderActivity>(`/api/workspaces/${id}/insiders`),
  getThemes: (id: string) => requestOrNull<ThemeScan>(`/api/workspaces/${id}/themes`),
  getNews: (id: string) => requestOrNull<NewsSignals>(`/api/workspaces/${id}/news`),

  // Automations
  getFilingWatch: (id: string) => requestOrNull<FilingWatch>(`/api/workspaces/${id}/filing-watch`),
  refreshWorkspace: (id: string) =>
    request<WorkspaceOverview>(`/api/workspaces/${id}/refresh`, { method: "POST" }),
  autoComps: (id: string) =>
    request<ComparableCompany[]>(`/api/workspaces/${id}/comps/auto`, { method: "POST" }),
};
