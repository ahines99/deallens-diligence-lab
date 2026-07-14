import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ActorProvider } from "@/components/identity/ActorContext";
import type { PortfolioDashboard } from "@/lib/types";
import { PortfolioCommandCenter } from "./PortfolioCommandCenter";

const apiMocks = vi.hoisted(() => ({
  listOrganizations: vi.fn(),
  listFunds: vi.fn(),
  getPortfolio: vi.fn(),
  exportPortfolioCsv: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

const emptyDashboard: PortfolioDashboard = {
  organization_id: "org-1",
  generated_at: "2026-07-13T14:00:00Z",
  filters: { search: null, stage: null, fund_id: null, as_of: "2026-07-13", ic_window_days: 30 },
  headline: {
    deals: 0,
    active_deals: 0,
    funds: 0,
    at_ic: 0,
    ic_next_30_days: 0,
    overdue_tasks: 0,
    critical_risks: 0,
    open_conditions: 0,
    average_readiness: 0,
  },
  stage_funnel: [],
  sector_exposure: [],
  strategy_exposure: [],
  deals: [],
  upcoming_ic: [],
  overdue_tasks: [],
  workstream_health: [],
  diligence_sla: [],
  critical_risks: [],
  conditions_to_close: [],
  team_workload: [],
  returns_snapshots: [],
  downside_watchlist: [],
  covenant_watchlist: [],
  import_exceptions: [],
};

describe("PortfolioCommandCenter", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiMocks.listOrganizations.mockReset();
    apiMocks.listFunds.mockReset();
    apiMocks.getPortfolio.mockReset();
    apiMocks.exportPortfolioCsv.mockReset();
  });

  it("shows an honest empty portfolio after resolving the selected organization", async () => {
    apiMocks.listOrganizations.mockResolvedValue([
      { id: "org-1", name: "Northbridge Capital", slug: "northbridge" },
    ]);
    apiMocks.listFunds.mockResolvedValue([]);
    apiMocks.getPortfolio.mockResolvedValue(emptyDashboard);

    render(<ActorProvider><PortfolioCommandCenter /></ActorProvider>);

    expect(await screen.findByText("No portfolio deals yet")).toBeInTheDocument();
    expect(screen.getByText("Northbridge Capital", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Average readiness").parentElement).toHaveTextContent("—");
    expect(screen.getByText(/Create a fund-scoped deal in Pipeline/)).toBeInTheDocument();
    expect(apiMocks.getPortfolio).toHaveBeenCalledWith(
      "org-1",
      { icWindowDays: 30 },
      expect.objectContaining({ actorId: "demo-associate" }),
    );
  });

  it("routes users to pipeline setup when no organizations are available", async () => {
    apiMocks.listOrganizations.mockResolvedValue([]);
    render(<ActorProvider><PortfolioCommandCenter /></ActorProvider>);

    expect(await screen.findByText("No organization access")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open pipeline setup" })).toHaveAttribute("href", "/pipeline");
  });
});
