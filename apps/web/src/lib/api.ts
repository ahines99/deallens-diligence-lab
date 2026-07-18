// DealLens API client. Thin typed wrapper over fetch against the FastAPI backend.
// Server Components call these directly; Client Components use the same functions.

import type {
  ExampleDealResult,
  ExampleTemplateInfo,
  FilingsQAResult,
  MemoFaithfulnessReport,
  Workspace,
  WorkspaceBuildStatus,
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
  InsiderPatterns,
  SegmentRevenue,
  InstitutionalOwnership,
  ActivistStakes,
  DebtMaturitySchedule,
  GovernanceProfile,
  CompSimilarity,
  RiskDiff,
  CrossCorpusQA,
  ModelQuality,
  AgentRun,
  AgentMemoDraft,
  AgentComparativeRun,
  NotificationDigest,
  InboxAging,
  PeerBenchmark,
  DilutionAnalysis,
  SotpResult,
  LitigationProfile,
  MacroMcPresets,
  SensitivityTornadoResult,
  DividendRecapResult,
  FacilitySizingResult,
  FundMonteCarloResult,
  AnnualValueCreationResult,
  WorkspaceSearchResult,
  QuotaUsage,
  SignalsOverview,
  FundConstruction,
  WatchlistEntry,
  Comment,
  ReviewInbox,
  AuditEvent,
  MemoRedline,
  ShareLink,
  ShareLinkCreated,
  SharedWorkspaceSnapshot,
  MembershipPermissions,
  CovenantHeadroomResult,
  CaseVarianceResult,
  ExitReadinessResult,
  FootballFieldResult,
  NotificationItem,
  ThemeScan,
  NewsSignals,
  FilingWatch,
  Memo,
  RedTeam,
  SecSearchResult,
  HealthStatus,
  DealType,
  SourceSnapshot,
  AccountMapping,
  CanonicalFinancialFact,
  FinancialReconciliation,
  FinancialImportException,
  FinancialImportResult,
  QoEAdjustment,
  QoEBridge,
  UnderwritingAssumptions,
  UnderwritingResult,
  UnderwritingCaseVersion,
  CaseKey,
  SensitivityVariable,
  SensitivityResult,
  ReverseStressResult,
  WorkingCapitalObservation,
  WorkingCapitalPegResult,
  ValuationReference,
  ValuationTriangulationResult,
  Organization,
  Fund,
  Deal,
  DealStage,
  StageGate,
  TeamMember,
  DealWorkstream,
  DealMilestone,
  DealTask,
  DiligenceRequestRecord,
  LedgerEntry,
  ICPacket,
  ReadinessResult,
  ICComment,
  ICCondition,
  ExportManifest,
  WorkflowAuditEvent,
  WorkflowActor,
  DataRoomDocument,
  CitedQARun,
  ClaimCategory,
  StructuredClaim,
  ClaimCollection,
  DocumentComparison,
  IntelligenceEvaluation,
  RedactionDecisionResult,
  RedactionProposal,
  RedactionSpan,
  RedactionStatus,
  PortfolioDashboard,
  PortfolioQuery,
  AuthSessionToken,
  CurrentIdentity,
  LoginInput,
  RegistrationInput,
  WorkspaceGovernancePatch,
  GovernedICPacketCreate,
} from "./types";
import { clearAuthSession } from "./authSession";
import { clearServerAuthSession } from "./authBridge";

const publicApiBase = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
const serverApiBase = (
  process.env.API_URL_INTERNAL ||
  process.env.SERVER_API_URL ||
  publicApiBase ||
  "http://localhost:8000"
).replace(/\/$/, "");

// Browser requests can use the same-origin proxy when no public API URL is supplied.
// Server Components always prefer the container-internal URL.
export const API_BASE = typeof window === "undefined" ? serverApiBase : "/backend";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

type FetchOpts = {
  method?: string;
  body?: unknown;
  cache?: RequestCache;
  headers?: Record<string, string>;
  auth?: "auto" | "omit";
};

type ServerAuthorizationProvider = () => string | null | Promise<string | null>;

// The shared client stays free of Next server-only imports. Server entry points
// install a request-scoped cookie reader through serverApi.ts instead.
let serverAuthorizationProvider: ServerAuthorizationProvider | null = null;

export function configureServerAuthorizationProvider(provider: ServerAuthorizationProvider) {
  if (typeof window === "undefined") serverAuthorizationProvider = provider;
}

async function withSessionAuthorization(
  headers: Record<string, string>,
  auth: FetchOpts["auth"] = "auto",
) {
  let authorization: string | null = null;
  if (auth !== "omit" && typeof window === "undefined") {
    try {
      authorization = serverAuthorizationProvider
        ? await serverAuthorizationProvider()
        : null;
    } catch {
      authorization = null;
    }
  }
  return {
    headers: authorization ? { Authorization: authorization, ...headers } : headers,
    authenticated: Boolean(authorization) || (typeof window !== "undefined" && auth !== "omit"),
  };
}

async function request<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { method = "GET", body, cache = "no-store", headers = {}, auth = "auto" } = opts;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const authorized = await withSessionAuthorization(
    body && !isForm ? { "Content-Type": "application/json", ...headers } : headers,
    auth,
  );
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: authorized.headers,
      body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
      cache,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the API service. Is the backend running?");
  }
  if (!res.ok) {
    if (res.status === 401 && authorized.authenticated) {
      clearAuthSession();
      void clearServerAuthSession();
    }
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

function workflowHeaders(actor: WorkflowActor = {}): Record<string, string> {
  const headers: Record<string, string> = {};
  const actorId = actor.actorId || process.env.NEXT_PUBLIC_ACTOR_ID;
  const actorName = actor.actorName || process.env.NEXT_PUBLIC_ACTOR_NAME;
  const organizationId = actor.organizationId || process.env.NEXT_PUBLIC_ORGANIZATION_ID;
  const roles = actor.roles?.join(",") || process.env.NEXT_PUBLIC_ACTOR_ROLES;
  if (actorId) headers["X-Actor-ID"] = actorId;
  if (actorName) headers["X-Actor-Name"] = actorName;
  if (organizationId) headers["X-Organization-ID"] = organizationId;
  if (roles) headers["X-Actor-Roles"] = roles;
  return headers;
}

function workflowRequest<T>(path: string, opts: FetchOpts = {}, actor: WorkflowActor = {}) {
  return request<T>(path, { ...opts, headers: { ...workflowHeaders(actor), ...opts.headers } });
}

async function workflowDownload(path: string, body: unknown, actor: WorkflowActor = {}) {
  const authorized = await withSessionAuthorization({ "Content-Type": "application/json", ...workflowHeaders(actor) });
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authorized.headers, body: JSON.stringify(body) });
  if (!response.ok) {
    if (response.status === 401 && authorized.authenticated) {
      clearAuthSession();
      void clearServerAuthSession();
    }
    let detail = response.statusText;
    try { const data = await response.json(); detail = data.detail || detail; } catch { /* no-op */ }
    throw new ApiError(response.status, detail);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || "ic-packet";
  return { blob: await response.blob(), filename, exportId: response.headers.get("X-Export-ID"), contentHash: response.headers.get("X-Content-SHA256") };
}

async function workflowGetDownload(path: string, actor: WorkflowActor = {}) {
  let response: Response;
  const authorized = await withSessionAuthorization(workflowHeaders(actor));
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "GET",
      headers: authorized.headers,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Cannot reach the API service. Is the backend running?");
  }
  if (!response.ok) {
    if (response.status === 401 && authorized.authenticated) {
      clearAuthSession();
      void clearServerAuthSession();
    }
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch {
      /* no-op */
    }
    throw new ApiError(response.status, detail);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || "portfolio.csv";
  return { blob: await response.blob(), filename };
}

function portfolioQueryPath(
  organizationId: string,
  suffix: string,
  filters: PortfolioQuery = {},
) {
  const params = new URLSearchParams();
  if (filters.search?.trim()) params.set("search", filters.search.trim());
  if (filters.stage) params.set("stage", filters.stage);
  if (filters.fundId) params.set("fund_id", filters.fundId);
  if (filters.asOf) params.set("as_of", filters.asOf);
  if (filters.icWindowDays !== undefined) {
    params.set("ic_window_days", String(filters.icWindowDays));
  }
  const query = params.toString();
  return `/api/organizations/${organizationId}/portfolio${suffix}${query ? `?${query}` : ""}`;
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
  health: () => request<HealthStatus>("/api/health", { auth: "omit" }),

  register: (body: RegistrationInput) =>
    request<AuthSessionToken>("/api/auth/register", { method: "POST", body, auth: "omit" }),
  login: (body: LoginInput) =>
    request<AuthSessionToken>("/api/auth/login", { method: "POST", body, auth: "omit" }),
  startDemoSession: () =>
    request<AuthSessionToken>("/api/auth/demo", { method: "POST", auth: "omit" }),
  currentIdentity: () => request<CurrentIdentity>("/api/auth/me"),
  logout: () => request<{ revoked: boolean }>("/api/auth/logout", { method: "POST" }),
  switchOrganization: (organizationId: string) =>
    request<AuthSessionToken>("/api/auth/switch-organization", { method: "POST", body: { organization_id: organizationId } }),

  listWorkspaces: () => request<Workspace[]>("/api/workspaces"),
  updateWorkspaceGovernance: (id: string, body: WorkspaceGovernancePatch) =>
    request<Workspace>(`/api/workspaces/${id}/governance`, { method: "PATCH", body }),
  createWorkspace: (body: {
    ticker?: string;
    name?: string;
    deal_type: DealType;
    investment_question?: string;
  }) => request<Workspace>("/api/workspaces", { method: "POST", body }),
  getWorkspace: (id: string) => request<WorkspaceOverview>(`/api/workspaces/${id}`),
  getBuildStatus: (id: string) =>
    request<WorkspaceBuildStatus>(`/api/workspaces/${id}/build-status`),
  retryBuild: (id: string) =>
    request<WorkspaceBuildStatus>(`/api/workspaces/${id}/build/retry`, { method: "POST" }),

  loadExampleDeal: (actor: WorkflowActor = {}) =>
    workflowRequest<ExampleDealResult>("/api/examples/private-deal", { method: "POST" }, actor),
  listExampleTemplates: () => request<ExampleTemplateInfo[]>("/api/examples/templates"),

  askFilings: (id: string, question: string) =>
    request<FilingsQAResult>(`/api/workspaces/${id}/qa`, { method: "POST", body: { question } }),
  getMemoFaithfulness: (id: string) =>
    request<MemoFaithfulnessReport>(`/api/workspaces/${id}/memo/faithfulness`),

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
  getInsiderPatterns: (id: string) =>
    requestOrNull<InsiderPatterns>(`/api/workspaces/${id}/insider-patterns`),
  getSegments: (id: string) =>
    requestOrNull<SegmentRevenue>(`/api/workspaces/${id}/financials/segments`),
  getInstitutionalOwnership: (id: string) =>
    requestOrNull<InstitutionalOwnership>(`/api/workspaces/${id}/institutional-ownership`),
  getActivistStakes: (id: string) =>
    requestOrNull<ActivistStakes>(`/api/workspaces/${id}/activist-stakes`),
  getDebtMaturities: (id: string) =>
    requestOrNull<DebtMaturitySchedule>(`/api/workspaces/${id}/debt-maturities`),
  getGovernanceProfile: (id: string) =>
    requestOrNull<GovernanceProfile>(`/api/workspaces/${id}/governance-profile`),
  buildGovernanceProfile: (id: string) =>
    request<GovernanceProfile>(`/api/workspaces/${id}/governance-profile`, { method: "POST" }),
  getCompSimilarity: (id: string) =>
    requestOrNull<CompSimilarity>(`/api/workspaces/${id}/comps/similarity`),
  getRiskDiff: (id: string) =>
    requestOrNull<RiskDiff>(`/api/workspaces/${id}/filings/risk-diff`),
  crossCorpusQA: (id: string, question: string, grounded = false) =>
    request<CrossCorpusQA>(`/api/workspaces/${id}/cross-corpus-qa`, { method: "POST", body: { question, grounded } }),
  getModelQuality: () => request<ModelQuality>(`/api/model-ops/quality`),
  runDiligenceAgent: (id: string, objective: string, maxSteps = 8, clientRequestId?: string) =>
    request<AgentRun>(`/api/workspaces/${id}/agent/run`, {
      method: "POST",
      body: {
        objective,
        max_steps: maxSteps,
        ...(clientRequestId ? { client_request_id: clientRequestId } : {}),
      },
    }),
  listAgentRuns: (id: string, limit = 10) =>
    request<AgentRun[]>(`/api/workspaces/${id}/agent/runs?limit=${limit}`),
  runAgentMemoDraft: (id: string, maxStepsPerSection = 6) =>
    request<AgentMemoDraft>(`/api/workspaces/${id}/agent-memo/draft`, {
      method: "POST",
      body: { max_steps_per_section: maxStepsPerSection },
    }),
  getAgentMemoDraft: (id: string) => requestOrNull<AgentMemoDraft>(`/api/workspaces/${id}/agent-memo`),
  getPeerBenchmark: (id: string) => requestOrNull<PeerBenchmark>(`/api/workspaces/${id}/peer-benchmark`),
  runSotp: (id: string, body: { multiples: Record<string, number>; default_multiple?: number; residual_multiple?: number }) =>
    request<SotpResult>(`/api/workspaces/${id}/sotp`, { method: "POST", body }),
  runSensitivityTornado: (id: string, body: Record<string, unknown>) =>
    request<SensitivityTornadoResult>(`/api/workspaces/${id}/underwriting/sensitivity-tornado`, { method: "POST", body }),
  solveDividendRecap: (id: string, body: Record<string, unknown>) =>
    request<DividendRecapResult>(`/api/workspaces/${id}/underwriting/dividend-recap-solve`, { method: "POST", body }),
  calculateFacilitySizing: (id: string, body: Record<string, unknown>) =>
    request<FacilitySizingResult>(`/api/workspaces/${id}/underwriting/facility-sizing`, { method: "POST", body }),
  runFundMonteCarlo: (id: string, body: Record<string, unknown>) =>
    request<FundMonteCarloResult>(`/api/workspaces/${id}/underwriting/fund-monte-carlo`, { method: "POST", body }),
  calculateAnnualValueCreation: (id: string, body: Record<string, unknown>) =>
    request<AnnualValueCreationResult>(`/api/workspaces/${id}/underwriting/annual-value-creation`, { method: "POST", body }),
  getLitigation: (id: string) => requestOrNull<LitigationProfile>(`/api/workspaces/${id}/litigation`),
  getMacroMcPresets: (id: string) => requestOrNull<MacroMcPresets>(`/api/workspaces/${id}/macro-mc-presets`),
  getDilution: (id: string) => requestOrNull<DilutionAnalysis>(`/api/workspaces/${id}/dilution`),
  getNotificationDigest: (organizationId: string, window: "daily" | "weekly" = "daily") =>
    request<NotificationDigest>(
      `/api/organizations/${organizationId}/notifications/digest?window=${window}`,
    ),
  getReviewAging: (organizationId: string, actor: WorkflowActor = {}) =>
    workflowRequest<InboxAging>(
      `/api/organizations/${organizationId}/my-reviews/aging`,
      {},
      actor,
    ),
  runComparativeAgent: (
    id: string,
    objective: string,
    compWorkspaceIds: string[],
    maxStepsPerWorkspace = 6,
  ) =>
    request<AgentComparativeRun>(`/api/workspaces/${id}/agent/compare`, {
      method: "POST",
      body: {
        objective,
        comp_workspace_ids: compWorkspaceIds,
        max_steps_per_workspace: maxStepsPerWorkspace,
      },
    }),
  decideAgentMemoSection: (
    id: string,
    draftId: string,
    section: string,
    decision: "accept" | "reject",
    actor: WorkflowActor = {},
  ) =>
    workflowRequest<AgentMemoDraft>(
      `/api/workspaces/${id}/agent-memo/${draftId}/sections/decide`,
      { method: "POST", body: { section, decision } },
      actor,
    ),
  searchWorkspace: (id: string, q: string) =>
    request<WorkspaceSearchResult>(`/api/workspaces/${id}/search?q=${encodeURIComponent(q)}`),
  getQuotaUsage: (org: string) =>
    request<QuotaUsage>(`/api/organizations/${org}/quota-usage`),
  getSignalsOverview: (id: string) =>
    requestOrNull<SignalsOverview>(`/api/workspaces/${id}/signals-overview`),
  getFundConstruction: (org: string, fundId?: string) =>
    request<FundConstruction>(`/api/organizations/${org}/fund-construction${fundId ? `?fund_id=${fundId}` : ""}`),
  listWatchlist: (org: string) =>
    request<WatchlistEntry[]>(`/api/organizations/${org}/watchlist`),
  addWatchlist: (org: string, body: { ticker?: string; cik?: string }) =>
    request<WatchlistEntry>(`/api/organizations/${org}/watchlist`, { method: "POST", body }),
  removeWatchlist: (id: string) =>
    request<void>(`/api/watchlist/${id}`, { method: "DELETE" }),

  // Collaboration (Wave 4 Batch 8)
  listComments: (entityType: string, entityId: string) =>
    request<Comment[]>(`/api/comments?entity_type=${entityType}&entity_id=${entityId}`),
  createComment: (body: { entity_type: string; entity_id: string; body: string; parent_comment_id?: string }) =>
    request<Comment>(`/api/comments`, { method: "POST", body }),
  resolveComment: (id: string) =>
    request<Comment>(`/api/comments/${id}/resolve`, { method: "POST" }),
  myReviews: (org: string) =>
    request<ReviewInbox>(`/api/organizations/${org}/my-reviews`),
  listAuditEvents: (org: string, query = "") =>
    request<AuditEvent[]>(`/api/organizations/${org}/audit-events${query}`),
  getMemoRedline: (id: string, runA: string, runB: string) =>
    request<MemoRedline>(`/api/workspaces/${id}/memo-redline?run_a=${runA}&run_b=${runB}`),
  createShareLink: (id: string, body: { label?: string; expires_at?: string }) =>
    request<ShareLinkCreated>(`/api/workspaces/${id}/share-links`, { method: "POST", body }),
  listShareLinks: (id: string) =>
    request<ShareLink[]>(`/api/workspaces/${id}/share-links`),
  revokeShareLink: (id: string) =>
    request<ShareLink>(`/api/share-links/${id}/revoke`, { method: "POST" }),
  // Public, session-less (the dsh_ token IS the authorization) — never send credentials.
  getSharedSnapshot: (token: string) =>
    request<SharedWorkspaceSnapshot>(`/api/shared/${encodeURIComponent(token)}`, { auth: "omit" }),

  // Identity extensions (Wave 4 Batch 9)
  oidcLogin: () => request<{ authorize_url: string; state: string }>(`/api/auth/oidc/login`, { auth: "omit" }),
  getMembershipPermissions: (membershipId: string) =>
    request<MembershipPermissions>(`/api/memberships/${membershipId}/permissions`),
  setMembershipPermission: (membershipId: string, body: { capability: string; granted: boolean }) =>
    request<MembershipPermissions>(`/api/memberships/${membershipId}/permissions`, { method: "PUT", body }),

  // Underwriting analytics (Wave 4)
  covenantHeadroom: (id: string, body: unknown) =>
    request<CovenantHeadroomResult>(`/api/workspaces/${id}/underwriting/covenant-headroom`, { method: "POST", body }),
  caseVariance: (id: string, body: unknown) =>
    request<CaseVarianceResult>(`/api/workspaces/${id}/underwriting/case-variance`, { method: "POST", body }),
  exitReadiness: (id: string, body: unknown) =>
    request<ExitReadinessResult>(`/api/workspaces/${id}/underwriting/exit-readiness`, { method: "POST", body }),
  footballField: (id: string, body: unknown) =>
    request<FootballFieldResult>(`/api/workspaces/${id}/underwriting/football-field`, { method: "POST", body }),

  // Notifications (Wave 4)
  listNotifications: (org: string, unreadOnly = false) =>
    request<NotificationItem[]>(`/api/organizations/${org}/notifications${unreadOnly ? "?unread_only=true" : ""}`),
  notificationsUnreadCount: (org: string) =>
    request<{ organization_id: string; unread: number }>(`/api/organizations/${org}/notifications/unread-count`),
  markNotificationRead: (id: string) =>
    request<NotificationItem>(`/api/notifications/${id}/read`, { method: "POST" }),
  getThemes: (id: string) => requestOrNull<ThemeScan>(`/api/workspaces/${id}/themes`),
  getNews: (id: string) => requestOrNull<NewsSignals>(`/api/workspaces/${id}/news`),

  // Automations
  getFilingWatch: (id: string) => requestOrNull<FilingWatch>(`/api/workspaces/${id}/filing-watch`),
  refreshWorkspace: (id: string) =>
    request<WorkspaceOverview>(`/api/workspaces/${id}/refresh`, { method: "POST" }),
  autoComps: (id: string) =>
    request<ComparableCompany[]>(`/api/workspaces/${id}/comps/auto`, { method: "POST" }),

  // Private-company data and source governance
  createPrivateTarget: (
    id: string,
    body: { name: string; sector?: string; description?: string; fiscal_year_end?: string | null },
  ) => request<Target>(`/api/workspaces/${id}/underwriting/private-target`, { method: "POST", body }),
  getSources: (id: string) =>
    request<SourceSnapshot[]>(`/api/workspaces/${id}/underwriting/sources`),
  getAccountMappings: (id: string) =>
    request<AccountMapping[]>(`/api/workspaces/${id}/underwriting/account-mappings`),
  createAccountMapping: (
    id: string,
    body: {
      source_type?: string;
      raw_account: string;
      canonical_account: string;
      statement: "income_statement" | "balance_sheet" | "cash_flow" | "kpi";
      sign_multiplier?: number;
      status?: "draft" | "approved" | "rejected";
      created_by?: string;
      approved_by?: string | null;
    },
    actor: WorkflowActor = {},
  ) => workflowRequest<AccountMapping>(`/api/workspaces/${id}/underwriting/account-mappings`, { method: "POST", body }, actor),
  importFinancialCsv: (id: string, file: File, metadata: { sourceName?: string; createdBy?: string } = {}, actor: WorkflowActor = {}) => {
    const body = new FormData();
    body.append("file", file);
    if (metadata.sourceName) body.append("source_name", metadata.sourceName);
    if (metadata.createdBy) body.append("created_by", metadata.createdBy);
    return workflowRequest<FinancialImportResult>(`/api/workspaces/${id}/underwriting/financial-imports/csv`, { method: "POST", body }, actor);
  },
  importFinancialXlsx: (id: string, file: File, metadata: { sourceName?: string; createdBy?: string } = {}, actor: WorkflowActor = {}) => {
    const body = new FormData();
    body.append("file", file);
    if (metadata.sourceName) body.append("source_name", metadata.sourceName);
    if (metadata.createdBy) body.append("created_by", metadata.createdBy);
    return workflowRequest<FinancialImportResult>(`/api/workspaces/${id}/underwriting/financial-imports/xlsx`, { method: "POST", body }, actor);
  },
  getFinancialFacts: (id: string, limit = 500) =>
    request<CanonicalFinancialFact[]>(`/api/workspaces/${id}/underwriting/financial-facts?limit=${limit}`),
  getImportExceptions: (id: string) =>
    request<FinancialImportException[]>(`/api/workspaces/${id}/underwriting/import-exceptions`),
  resolveImportException: (id: string, exceptionId: string, resolvedBy: string, actor: WorkflowActor = {}) =>
    workflowRequest<FinancialImportException>(`/api/workspaces/${id}/underwriting/import-exceptions/${exceptionId}/resolve`, { method: "POST", body: { resolved_by: resolvedBy } }, actor),
  getReconciliations: (id: string) =>
    request<FinancialReconciliation[]>(`/api/workspaces/${id}/underwriting/reconciliations`),
  getQoEAdjustments: (id: string) =>
    request<QoEAdjustment[]>(`/api/workspaces/${id}/underwriting/qoe-adjustments`),
  createQoEAdjustment: (id: string, body: {
    period_start?: string | null; period_end: string; bridge_layer: "management" | "sponsor" | "covenant";
    title: string; description?: string; category?: string; amount: number; currency?: string;
    is_recurring?: boolean; is_run_rate?: boolean; is_cash?: boolean; owner?: string;
    evidence_ref?: string | null; source_snapshot_id?: string | null; source_locator?: string | null; created_by?: string;
  }, actor: WorkflowActor = {}) => workflowRequest<QoEAdjustment>(`/api/workspaces/${id}/underwriting/qoe-adjustments`, { method: "POST", body }, actor),
  decideQoEAdjustment: (id: string, adjustmentId: string, decision: "approve" | "reject", decidedBy: string, note = "", actor: WorkflowActor = {}) =>
    workflowRequest<QoEAdjustment>(`/api/workspaces/${id}/underwriting/qoe-adjustments/${adjustmentId}/decision`, { method: "POST", body: { decision, decided_by: decidedBy, note } }, actor),
  getQoEBridge: (id: string) => requestOrNull<QoEBridge>(`/api/workspaces/${id}/underwriting/qoe-bridge`),

  // Versioned underwriting and stress testing
  calculateUnderwriting: (id: string, assumptions: UnderwritingAssumptions) =>
    request<UnderwritingResult>(`/api/workspaces/${id}/underwriting/calculate`, { method: "POST", body: { assumptions } }),
  getUnderwritingCases: (id: string) =>
    request<UnderwritingCaseVersion[]>(`/api/workspaces/${id}/underwriting/cases`),
  getUnderwritingCaseVersions: (id: string, caseKey: CaseKey) =>
    request<UnderwritingCaseVersion[]>(`/api/workspaces/${id}/underwriting/cases/${caseKey}/versions`),
  createUnderwritingCaseSet: (id: string, cases: { case_key: CaseKey; label: string; assumptions: UnderwritingAssumptions; expected_parent_version?: number; created_by?: string; change_note?: string }[]) =>
    request<UnderwritingCaseVersion[]>(`/api/workspaces/${id}/underwriting/case-set`, { method: "POST", body: { cases } }),
  decideUnderwritingCase: (id: string, caseKey: CaseKey, version: number, decision: "submitted" | "approved" | "rejected" | "superseded", actor: string, rationale = "") =>
    request(`/api/workspaces/${id}/underwriting/cases/${caseKey}/versions/${version}/decisions`, { method: "POST", body: { decision, actor, rationale } }),
  runSensitivity: (id: string, body: { assumptions: UnderwritingAssumptions; rows: { variable: SensitivityVariable; values: number[] }; columns: { variable: SensitivityVariable; values: number[] }; metric: "irr" | "moic" | "minimum_liquidity" }) =>
    request<SensitivityResult>(`/api/workspaces/${id}/underwriting/sensitivity`, { method: "POST", body }),
  runReverseStress: (id: string, body: { assumptions: UnderwritingAssumptions; variable: SensitivityVariable; objective: "irr" | "moic" | "minimum_liquidity"; target: number; lower_bound: number; upper_bound: number }) =>
    request<ReverseStressResult>(`/api/workspaces/${id}/underwriting/reverse-stress`, { method: "POST", body }),
  calculateWorkingCapitalPeg: (id: string, body: { observations: WorkingCapitalObservation[]; closing_date: string; method: "median_ltm" | "average_ltm" | "seasonal_average"; delivered_working_capital?: number | null }) =>
    request<WorkingCapitalPegResult>(`/api/workspaces/${id}/underwriting/working-capital-peg`, { method: "POST", body }),
  triangulateValuation: (id: string, body: { ebitda: number; net_debt: number; dcf_enterprise_value?: number | null; public_comps: ValuationReference[]; precedent_transactions: ValuationReference[]; dcf_weight?: number; public_comps_weight?: number; precedents_weight?: number }) =>
    request<ValuationTriangulationResult>(`/api/workspaces/${id}/underwriting/valuation-triangulation`, { method: "POST", body }),

  // Tenant-aware deal workflow
  listOrganizations: (actor: WorkflowActor = {}) => workflowRequest<Organization[]>("/api/organizations", {}, actor),
  createOrganization: (body: { name: string; slug: string }, actor: WorkflowActor = {}) =>
    workflowRequest<Organization>("/api/organizations", { method: "POST", body }, actor),
  listFunds: (organizationId: string, actor: WorkflowActor = {}) =>
    workflowRequest<Fund[]>(`/api/organizations/${organizationId}/funds`, {}, { ...actor, organizationId }),
  getPortfolio: (organizationId: string, filters: PortfolioQuery = {}, actor: WorkflowActor = {}) =>
    workflowRequest<PortfolioDashboard>(portfolioQueryPath(organizationId, "", filters), {}, { ...actor, organizationId }),
  exportPortfolioCsv: (organizationId: string, filters: PortfolioQuery = {}, actor: WorkflowActor = {}) =>
    workflowGetDownload(portfolioQueryPath(organizationId, "/export.csv", filters), { ...actor, organizationId }),
  createFund: (organizationId: string, body: { name: string; vintage_year?: number | null; base_currency?: string; strategy?: string }, actor: WorkflowActor = {}) =>
    workflowRequest<Fund>(`/api/organizations/${organizationId}/funds`, { method: "POST", body }, { ...actor, organizationId }),
  listDeals: (organizationId: string, actor: WorkflowActor = {}) =>
    workflowRequest<Deal[]>(`/api/organizations/${organizationId}/deals`, {}, { ...actor, organizationId }),
  getWorkspaceDeal: (workspaceId: string, actor: WorkflowActor = {}) =>
    requestOrNull<Deal>(`/api/workspaces/${workspaceId}/deal`, { headers: workflowHeaders(actor) }),
  getDeal: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<Deal>(`/api/deals/${dealId}`, {}, actor),
  createDeal: (fundId: string, body: { code: string; name: string; target_company: string; deal_type?: string; workspace_id?: string | null; owner_actor_id?: string | null; ic_date?: string | null; summary?: string; seed_default_gates?: boolean }, actor: WorkflowActor = {}) =>
    workflowRequest<Deal>(`/api/funds/${fundId}/deals`, { method: "POST", body }, actor),
  updateDeal: (dealId: string, body: { expected_version: number; name?: string; target_company?: string; owner_actor_id?: string | null; ic_date?: string | null; summary?: string; status?: "active" | "on_hold" }, actor: WorkflowActor = {}) =>
    workflowRequest<Deal>(`/api/deals/${dealId}`, { method: "PATCH", body }, actor),
  transitionDeal: (dealId: string, toStage: DealStage, rationale = "", actor: WorkflowActor = {}) =>
    workflowRequest(`/api/deals/${dealId}/stage-transitions`, { method: "POST", body: { to_stage: toStage, rationale } }, actor),
  listGates: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<StageGate[]>(`/api/deals/${dealId}/gates`, {}, actor),
  resolveGate: (gateId: string, status: "satisfied" | "waived", resolutionNote = "", actor: WorkflowActor = {}) =>
    workflowRequest<StageGate>(`/api/stage-gates/${gateId}/resolve`, { method: "POST", body: { status, resolution_note: resolutionNote, evidence_refs: [] } }, actor),
  listTeam: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<TeamMember[]>(`/api/deals/${dealId}/team`, {}, actor),
  addTeamMember: (dealId: string, body: { actor_id: string; display_name: string; email?: string | null; role: string }, actor: WorkflowActor = {}) => workflowRequest<TeamMember>(`/api/deals/${dealId}/team`, { method: "POST", body }, actor),
  listWorkstreams: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<DealWorkstream[]>(`/api/deals/${dealId}/workstreams`, {}, actor),
  createWorkstream: (dealId: string, body: { slug: string; label: string; description?: string; lead_actor_id?: string | null; due_date?: string | null }, actor: WorkflowActor = {}) => workflowRequest<DealWorkstream>(`/api/deals/${dealId}/workstreams`, { method: "POST", body }, actor),
  updateWorkstream: (workstreamId: string, body: { status?: string; lead_actor_id?: string | null; due_date?: string | null; description?: string }, actor: WorkflowActor = {}) => workflowRequest<DealWorkstream>(`/api/workstreams/${workstreamId}`, { method: "PATCH", body }, actor),
  listMilestones: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<DealMilestone[]>(`/api/deals/${dealId}/milestones`, {}, actor),
  createMilestone: (dealId: string, body: { workstream_id?: string | null; title: string; description?: string; due_date?: string | null; owner_actor_id?: string | null }, actor: WorkflowActor = {}) => workflowRequest<DealMilestone>(`/api/deals/${dealId}/milestones`, { method: "POST", body }, actor),
  updateMilestone: (milestoneId: string, body: { status?: string; due_date?: string | null; owner_actor_id?: string | null }, actor: WorkflowActor = {}) => workflowRequest<DealMilestone>(`/api/milestones/${milestoneId}`, { method: "PATCH", body }, actor),
  listTasks: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<DealTask[]>(`/api/deals/${dealId}/tasks`, {}, actor),
  createTask: (dealId: string, body: { workstream_id?: string | null; title: string; description?: string; priority?: string; assignee_actor_id?: string | null; due_date?: string | null; dependency_task_ids?: string[] }, actor: WorkflowActor = {}) => workflowRequest<DealTask>(`/api/deals/${dealId}/tasks`, { method: "POST", body }, actor),
  updateTask: (taskId: string, body: { status?: string; assignee_actor_id?: string | null; due_date?: string | null; priority?: string; blocked_reason?: string | null }, actor: WorkflowActor = {}) => workflowRequest<DealTask>(`/api/tasks/${taskId}`, { method: "PATCH", body }, actor),
  listDiligenceRequests: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<DiligenceRequestRecord[]>(`/api/deals/${dealId}/diligence-requests`, {}, actor),
  createDiligenceRequest: (dealId: string, body: { workstream_id?: string | null; title: string; question: string; rationale?: string; priority?: string; owner_actor_id?: string | null; respondent_actor_id?: string | null; due_date?: string | null; send_now?: boolean }, actor: WorkflowActor = {}) => workflowRequest<DiligenceRequestRecord>(`/api/deals/${dealId}/diligence-requests`, { method: "POST", body }, actor),
  sendDiligenceRequest: (requestId: string, actor: WorkflowActor = {}) => workflowRequest<DiligenceRequestRecord>(`/api/diligence-requests/${requestId}/send`, { method: "POST" }, actor),
  respondToDiligenceRequest: (requestId: string, responseText: string, actor: WorkflowActor = {}) => workflowRequest(`/api/diligence-requests/${requestId}/responses`, { method: "POST", body: { response_text: responseText } }, actor),
  reviewDiligenceRequest: (requestId: string, action: "accept" | "reject", note = "", actor: WorkflowActor = {}) => workflowRequest<DiligenceRequestRecord>(`/api/diligence-requests/${requestId}/review`, { method: "POST", body: { action, note } }, actor),
  listLedger: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<LedgerEntry[]>(`/api/deals/${dealId}/ledger`, {}, actor),
  createLedgerEntry: (dealId: string, body: { entry_type: "thesis" | "issue" | "risk" | "decision"; title: string; description: string; status?: string; severity?: string; owner_actor_id?: string | null; evidence_refs?: string[]; related_artifact_ids?: string[] }, actor: WorkflowActor = {}) => workflowRequest<LedgerEntry>(`/api/deals/${dealId}/ledger`, { method: "POST", body }, actor),
  listICPackets: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<ICPacket[]>(`/api/deals/${dealId}/ic-packets`, {}, actor),
  createICPacket: (dealId: string, body: GovernedICPacketCreate, actor: WorkflowActor = {}) => workflowRequest<ICPacket>(`/api/deals/${dealId}/ic-packets`, { method: "POST", body }, actor),
  checkICReadiness: (packetId: string, actor: WorkflowActor = {}) => workflowRequest<ReadinessResult>(`/api/ic-packets/${packetId}/readiness`, { method: "POST" }, actor),
  submitICPacket: (packetId: string, actor: WorkflowActor = {}) => workflowRequest<ICPacket>(`/api/ic-packets/${packetId}/submit`, { method: "POST" }, actor),
  listICComments: (packetId: string, actor: WorkflowActor = {}) => workflowRequest<ICComment[]>(`/api/ic-packets/${packetId}/comments`, {}, actor),
  addICComment: (packetId: string, body: { section_path?: string; body: string; blocking?: boolean }, actor: WorkflowActor = {}) => workflowRequest<ICComment>(`/api/ic-packets/${packetId}/comments`, { method: "POST", body }, actor),
  resolveICComment: (commentId: string, resolution: string, actor: WorkflowActor = {}) => workflowRequest<ICComment>(`/api/ic-comments/${commentId}/resolve`, { method: "POST", body: { resolution } }, actor),
  recordICDecision: (packetId: string, body: { decision: "approve" | "conditional" | "defer" | "decline"; rationale: string; meeting_at?: string | null; conditions?: { description: string; owner_actor_id?: string | null; due_date?: string | null }[] }, actor: WorkflowActor = {}) => workflowRequest(`/api/ic-packets/${packetId}/decisions`, { method: "POST", body }, actor),
  listConditions: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<ICCondition[]>(`/api/deals/${dealId}/conditions`, {}, actor),
  updateCondition: (conditionId: string, status: "satisfied" | "waived", resolutionNote = "", actor: WorkflowActor = {}) => workflowRequest<ICCondition>(`/api/conditions/${conditionId}`, { method: "PATCH", body: { status, resolution_note: resolutionNote, evidence_refs: [] } }, actor),
  diffICPackets: (fromPacketId: string, toPacketId: string, actor: WorkflowActor = {}) => workflowRequest<{ from_packet_id: string; to_packet_id: string; from_version: number; to_version: number; changes: { path: string; change: "added" | "removed" | "changed"; before: unknown; after: unknown }[] }>(`/api/ic-packets/${fromPacketId}/diff/${toPacketId}`, {}, actor),
  createExportManifest: (packetId: string, format: "pdf" | "docx" | "xlsx" | "json", actor: WorkflowActor = {}) => workflowRequest<ExportManifest>(`/api/ic-packets/${packetId}/export-manifests`, { method: "POST", body: { format } }, actor),
  exportICPacket: (packetId: string, format: "pdf" | "docx" | "xlsx" | "json", actor: WorkflowActor = {}) => workflowDownload(`/api/ic-packets/${packetId}/exports`, { format }, actor),
  listWorkflowAudit: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<WorkflowAuditEvent[]>(`/api/deals/${dealId}/audit-events`, {}, actor),

  // Deal-room intelligence and human-reviewed evidence
  listIntelligenceDocuments: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<DataRoomDocument[]>(`/api/deals/${dealId}/intelligence/documents`, {}, actor),
  uploadIntelligenceDocument: (dealId: string, file: File, metadata: { title?: string; logicalDocumentId?: string } = {}, actor: WorkflowActor = {}) => {
    const body = new FormData(); body.append("file", file); if (metadata.title) body.append("title", metadata.title); if (metadata.logicalDocumentId) body.append("logical_document_id", metadata.logicalDocumentId);
    return workflowRequest<DataRoomDocument>(`/api/deals/${dealId}/intelligence/documents/upload`, { method: "POST", body }, actor);
  },
  askDealRoom: (dealId: string, question: string, documentIds: string[] = [], actor: WorkflowActor = {}) => workflowRequest<CitedQARun>(`/api/deals/${dealId}/intelligence/qa`, { method: "POST", body: { question, filters: { document_ids: documentIds, latest_only: true } } }, actor),
  listQARuns: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<CitedQARun[]>(`/api/deals/${dealId}/intelligence/qa-runs`, {}, actor),
  extractClaims: (dealId: string, documentIds: string[], categories: ClaimCategory[], actor: WorkflowActor = {}) => workflowRequest<StructuredClaim[]>(`/api/deals/${dealId}/intelligence/extractions`, { method: "POST", body: { document_ids: documentIds, categories, min_confidence: 0.65, latest_only: true } }, actor),
  getClaims: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<ClaimCollection>(`/api/deals/${dealId}/intelligence/claims`, {}, actor),
  reviewClaim: (claimId: string, expectedRevision: number, action: "approve" | "reject", note = "", actor: WorkflowActor = {}) => workflowRequest(`/api/intelligence/claims/${claimId}/review`, { method: "POST", body: { action, expected_revision: expectedRevision, note } }, actor),
  compareIntelligenceDocuments: (dealId: string, fromDocumentId: string, toDocumentId: string, comparisonType: "change" | "contradiction", actor: WorkflowActor = {}) => workflowRequest<DocumentComparison>(`/api/deals/${dealId}/intelligence/comparisons`, { method: "POST", body: { from_document_id: fromDocumentId, to_document_id: toDocumentId, comparison_type: comparisonType } }, actor),
  listIntelligenceComparisons: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<DocumentComparison[]>(`/api/deals/${dealId}/intelligence/comparisons`, {}, actor),
  listIntelligenceEvaluations: (dealId: string, actor: WorkflowActor = {}) => workflowRequest<IntelligenceEvaluation[]>(`/api/deals/${dealId}/intelligence/evaluations`, {}, actor),

  // G75 four-eyes redaction workflow: propose spans against the LATEST document version;
  // a distinct human approves, minting the redacted next version (originals untouched).
  proposeRedaction: (dealId: string, documentId: string, spans: RedactionSpan[], note = "", actor: WorkflowActor = {}) => workflowRequest<RedactionProposal>(`/api/deals/${dealId}/intelligence/documents/${documentId}/redactions`, { method: "POST", body: { spans, note } }, actor),
  decideRedaction: (proposalId: string, decision: "approve" | "reject", note = "", actor: WorkflowActor = {}) => workflowRequest<RedactionDecisionResult>(`/api/intelligence/redactions/${proposalId}/decide`, { method: "POST", body: { decision, note } }, actor),
  listRedactions: (dealId: string, status?: RedactionStatus, actor: WorkflowActor = {}) => workflowRequest<RedactionProposal[]>(`/api/deals/${dealId}/intelligence/redactions${status ? `?status=${status}` : ""}`, {}, actor),
};
