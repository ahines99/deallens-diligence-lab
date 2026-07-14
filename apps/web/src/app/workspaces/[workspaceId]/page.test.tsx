import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  Deal,
  QoEBridge,
  RiskFinding,
  SourceSnapshot,
  Target,
  Workspace,
  WorkspaceOverview,
} from "@/lib/types";
import WorkspaceCockpit from "./page";

const apiMocks = vi.hoisted(() => ({
  getWorkspace: vi.fn(),
  getSources: vi.fn(),
  getQoEBridge: vi.fn(),
  getUnderwritingCases: vi.fn(),
  getWorkspaceDeal: vi.fn(),
  listTasks: vi.fn(),
  listDiligenceRequests: vi.fn(),
  listGates: vi.fn(),
  listLedger: vi.fn(),
  listICPackets: vi.fn(),
  listICComments: vi.fn(),
}));

vi.mock("@/lib/serverApi", () => ({
  api: apiMocks,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

const notFound = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ notFound }));

const workspace: Workspace = {
  id: "w1",
  name: "Atlas Underwrite",
  organization_id: "org-1",
  target_id: "t1",
  deal_type: "buyout",
  investment_question: "Should Fund V acquire Atlas Software?",
  status: "in_progress",
  data_classification: "confidential",
  external_llm_allowed: false,
  build_status: "ready",
  build_step: null,
  build_error: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-07-10T00:00:00Z",
};

const topRisk: RiskFinding = {
  id: "r1",
  workspace_id: "w1",
  risk_category: "customer_concentration",
  risk_category_label: "Customer concentration",
  title: "Top customer drives revenue",
  finding: "The largest customer represents 38% of trailing revenue.",
  severity: "high",
  severity_score: 8,
  likelihood: "high",
  confidence: 0.8,
  evidence_ref: "EV-001",
  follow_up_question: "What are the renewal terms for the top account?",
  workstream_owner: "commercial",
  created_at: "2026-07-01T00:00:00Z",
} as unknown as RiskFinding;

const overview: WorkspaceOverview = {
  workspace,
  target: {
    name: "Atlas Software",
    ticker: null,
    sector: "Vertical Software",
  } as unknown as Target,
  counts: { filings: 4, comps: 3, risks: 2, questions: 5, evidence: 6 },
  artifacts: { plan: true, risks: true, questions: true, ic_memo: false, bear_case: false },
  top_risks: [topRisk],
};

const source: SourceSnapshot = {
  id: "s1",
  workspace_id: "w1",
  target_id: "t1",
  source_kind: "financials",
  source_type: "management_financials",
  source_name: "Management P&L",
  version: 1,
  supersedes_id: null,
  filename: "atlas-pnl.xlsx",
  content_type: null,
  storage_uri: null,
  input_hash: "1".repeat(64),
  content_hash: "2".repeat(64),
  byte_size: 1024,
  record_count: 128,
  status: "ready",
  source_metadata: null,
  created_by: "associate@example.test",
  created_at: "2026-07-10T00:00:00Z",
  sealed_at: "2026-07-10T00:00:00Z",
};

const bridge = {
  status: "ready",
  sponsor_ebitda: 12_000_000,
  currency: "USD",
  period_end: "2025-12-31",
} as unknown as QoEBridge;

const deal = {
  id: "d1",
  organization_id: "org-1",
  fund_id: "f1",
  workspace_id: "w1",
  code: "ATL-101",
  name: "Project Atlas",
  target_company: "Atlas Software",
  stage: "diligence",
  status: "active",
} as unknown as Deal;

const renderCockpit = async () =>
  render(await WorkspaceCockpit({ params: Promise.resolve({ workspaceId: "w1" }) }));

describe("WorkspaceCockpit (deal overview page)", () => {
  beforeEach(() => {
    apiMocks.getWorkspace.mockResolvedValue(overview);
    apiMocks.getSources.mockResolvedValue([source]);
    apiMocks.getQoEBridge.mockResolvedValue(bridge);
    apiMocks.getUnderwritingCases.mockResolvedValue([]);
    apiMocks.getWorkspaceDeal.mockResolvedValue(deal);
    apiMocks.listTasks.mockResolvedValue([]);
    apiMocks.listDiligenceRequests.mockResolvedValue([]);
    apiMocks.listGates.mockResolvedValue([]);
    apiMocks.listLedger.mockResolvedValue([]);
    apiMocks.listICPackets.mockResolvedValue([]);
    apiMocks.listICComments.mockResolvedValue([]);
  });

  afterEach(cleanup);

  it("renders the KPI strip, readiness grid, top risks, and source snapshots", async () => {
    await renderCockpit();

    // KPI / metric strip.
    expect(screen.getByText("Normalized EBITDA")).toBeInTheDocument();
    expect(screen.getByText("$12M")).toBeInTheDocument();
    expect(screen.getByText("Base-case MOIC")).toBeInTheDocument();
    expect(screen.getByText("Base-case XIRR")).toBeInTheDocument();
    expect(screen.getByText("Open diligence")).toBeInTheDocument();
    expect(screen.getByText("IC status")).toBeInTheDocument();
    expect(screen.getByText("Not started")).toBeInTheDocument();

    // Readiness grid labels.
    expect(screen.getByText("Deal readiness")).toBeInTheDocument();
    for (const label of [
      "Underwriting data",
      "QoE bridge",
      "Three-case model",
      "Deal execution",
      "Evidence review",
      "IC governance",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("1 sealed source versions")).toBeInTheDocument();

    // Top risk finding with its evidence reference.
    expect(screen.getByText("Top customer drives revenue")).toBeInTheDocument();
    expect(
      screen.getByText("The largest customer represents 38% of trailing revenue."),
    ).toBeInTheDocument();
    expect(screen.getByText(/EV-001/)).toBeInTheDocument();

    // Source snapshot panel with the sealed source and its health badge.
    expect(screen.getByText("Latest source snapshots")).toBeInTheDocument();
    expect(screen.getByText("Management P&L")).toBeInTheDocument();
    expect(screen.getAllByText("ready").length).toBeGreaterThan(0);

    expect(notFound).not.toHaveBeenCalled();
  });

  it("shows explicit unavailable states instead of clean zeros when services fail", async () => {
    apiMocks.getSources.mockRejectedValue(new Error("sources down"));
    apiMocks.getQoEBridge.mockRejectedValue(new Error("qoe down"));
    apiMocks.getUnderwritingCases.mockRejectedValue(new Error("model down"));
    apiMocks.getWorkspaceDeal.mockRejectedValue(new Error("workflow down"));

    await renderCockpit();

    expect(screen.getByText("Source service unavailable")).toBeInTheDocument();
    expect(screen.getByText("QoE service unavailable")).toBeInTheDocument();
    expect(screen.getByText("Model service unavailable")).toBeInTheDocument();
    expect(screen.getByText("Workflow service unavailable")).toBeInTheDocument();
    expect(
      screen.getByText("Source health is unavailable. No clean status is inferred."),
    ).toBeInTheDocument();
    expect(screen.getByText("Model results unavailable")).toBeInTheDocument();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  });
});
