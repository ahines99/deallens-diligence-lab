import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceBuildProgress } from "./WorkspaceBuildProgress";
import type { WorkspaceBuildStatus } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/lib/api", () => ({
  api: { getBuildStatus: vi.fn(), retryBuild: vi.fn() },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

afterEach(cleanup);

const building: WorkspaceBuildStatus = {
  workspace_id: "w1",
  status: "building",
  step: "fetching_financials",
  error: null,
  ticker: "MSFT",
};

describe("WorkspaceBuildProgress", () => {
  it("shows the live step timeline while building", () => {
    render(<WorkspaceBuildProgress workspaceId="w1" initial={building} />);
    expect(screen.getByText(/Assembling the diligence pack for MSFT/)).toBeInTheDocument();
    expect(screen.getByText("Fetching XBRL financials")).toBeInTheDocument();
    expect(screen.getByText("Running analysis")).toBeInTheDocument();
    expect(screen.queryByText("Retry build")).not.toBeInTheDocument();
  });

  it("surfaces the failure reason and a retry action, never a clean empty state", () => {
    render(
      <WorkspaceBuildProgress
        workspaceId="w1"
        initial={{ ...building, status: "failed", error: "EDGAR timed out mid-ingest" }}
      />,
    );
    expect(screen.getByText(/did not complete/)).toBeInTheDocument();
    expect(screen.getByText("EDGAR timed out mid-ingest")).toBeInTheDocument();
    expect(screen.getByText("Retry build")).toBeInTheDocument();
  });
});
