import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentComparePanel } from "./AgentComparePanel";
import type { AgentComparativeRun } from "@/lib/types";

const apiMocks = vi.hoisted(() => ({
  runComparativeAgent: vi.fn(),
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

const base: AgentComparativeRun = {
  primary_workspace_id: "wsA",
  comp_workspace_ids: ["wsB"],
  objective: "How concentrated is customer revenue?",
  status: "completed",
  reason: "applied",
  blocking_workspace_id: null,
  per_workspace: [
    {
      workspace_id: "wsA",
      workspace_name: "Alpha Platforms",
      role: "primary",
      status: "completed",
      reason: "applied",
      answer: "The largest customer represents approximately 14 percent of revenue.",
      artifact_version_id: "artifact-alpha-1",
      tools_used: ["search_filings"],
      steps_used: 1,
      grounding: { grounded: true, numeric_violations: [], unknown_refs: [] },
    },
    {
      workspace_id: "wsB",
      workspace_name: "Beta Logistics",
      role: "comp",
      status: "rejected_ungrounded",
      reason: "grounding_failed",
      answer: null,
      artifact_version_id: "artifact-beta-1",
      tools_used: ["search_filings"],
      steps_used: 1,
      grounding: { grounded: false, numeric_violations: ["23%"], unknown_refs: [] },
    },
  ],
  merged_markdown:
    "## Alpha Platforms (wsA)\n\nThe largest customer represents approximately 14 percent of revenue.\n\n" +
    "## Beta Logistics (wsB)\n\n_withheld/failed: rejected_ungrounded (grounding_failed)_",
  grounding: { grounded: true, numeric_violations: [], unknown_refs: [] },
  artifact_version_id: "artifact-compare-1",
  generated_at: "2026-07-18T12:00:00Z",
};

async function runWith(run: AgentComparativeRun) {
  apiMocks.runComparativeAgent.mockResolvedValue(run);
  render(<AgentComparePanel workspaceId="wsA" />);
  fireEvent.change(screen.getByPlaceholderText(/How concentrated is customer revenue/), {
    target: { value: "How concentrated is customer revenue?" },
  });
  fireEvent.change(screen.getByPlaceholderText("Comp workspace ID 1"), {
    target: { value: "wsB" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: /Run comparative agent/ }).closest("form")!,
  );
  await waitFor(() => expect(apiMocks.runComparativeAgent).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentComparePanel", () => {
  it("sends the objective plus the entered comp workspace ids", async () => {
    await runWith(base);
    expect(apiMocks.runComparativeAgent).toHaveBeenCalledWith(
      "wsA",
      "How concentrated is customer revenue?",
      ["wsB"],
    );
  });

  it("renders per-workspace sections with provenance and the merged markdown", async () => {
    await runWith(base);
    expect(await screen.findByText("Alpha Platforms")).toBeDefined();
    expect(screen.getByText("Beta Logistics")).toBeDefined();
    expect(screen.getByText("primary")).toBeDefined();
    expect(screen.getByText("comp")).toBeDefined();
    // The withheld comp run is explicit — status, reason, and the exact ungrounded token.
    expect(screen.getByText(/Withheld\/failed: rejected ungrounded/)).toBeDefined();
    expect(screen.getByText(/23%/)).toBeDefined();
    // Merged markdown renders both provenance-labeled sections.
    expect(screen.getByText("Alpha Platforms (wsA)")).toBeDefined();
    expect(screen.getByText("Beta Logistics (wsB)")).toBeDefined();
    expect(
      screen.getAllByText(/approximately 14 percent of revenue/).length,
    ).toBeGreaterThanOrEqual(2); // per-workspace section + merged answer
    expect(screen.getByText("Union grounding gate passed")).toBeDefined();
    expect(screen.getByText(/Sealed comparative record/)).toBeDefined();
  });

  it("explains a not_run gating and names the blocking workspace", async () => {
    await runWith({
      ...base,
      status: "not_run",
      reason: "no_consent",
      blocking_workspace_id: "wsB",
      per_workspace: [],
      merged_markdown: null,
      grounding: null,
      artifact_version_id: null,
    });
    expect(await screen.findByText("Comparative run did not run")).toBeDefined();
    expect(screen.getByText("wsB")).toBeDefined();
    expect(screen.getByText(/no workspace is ever silently excluded/)).toBeDefined();
    expect(screen.queryByText(/approximately 14 percent/)).toBeNull();
  });
});
