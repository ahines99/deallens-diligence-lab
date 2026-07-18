import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentMemoPanel, type AgentMemoDraft } from "./AgentMemoPanel";

const apiMocks = vi.hoisted(() => ({
  runAgentMemoDraft: vi.fn(),
  getAgentMemoDraft: vi.fn(),
  decideAgentMemoSection: vi.fn(),
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

const inReview: AgentMemoDraft = {
  workspace_id: "w1",
  status: "in_review",
  reason: null,
  sections: [
    {
      section: "Business overview",
      status: "drafted",
      answer: "The largest customer represents approximately 14 percent of revenue.",
      grounding: { grounded: true, numeric_violations: [], unknown_refs: [] },
      artifact_version_id: "run-artifact-1",
      decision: "pending",
      decided_by: null,
      decided_at: null,
    },
    {
      section: "Financial performance",
      status: "withheld",
      answer: null,
      grounding: { grounded: false, numeric_violations: ["37%"], unknown_refs: [] },
      artifact_version_id: "run-artifact-2",
      decision: "pending",
      decided_by: null,
      decided_at: null,
    },
  ],
  generated_at: "2026-07-18T12:00:00Z",
  draft_artifact_id: "draft-artifact-1",
  version: 1,
  assembled_markdown: null,
};

const decided: AgentMemoDraft = {
  ...inReview,
  status: "decided",
  version: 2,
  draft_artifact_id: "draft-artifact-2",
  sections: [
    {
      ...inReview.sections[0],
      decision: "accept",
      decided_by: "analyst-1",
      decided_at: "2026-07-18T12:05:00Z",
    },
    inReview.sections[1],
  ],
  assembled_markdown:
    "## Business overview\n\nThe largest customer represents approximately 14 percent of revenue.",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentMemoPanel", () => {
  it("renders drafted and withheld sections with the grounding-gate callout", async () => {
    apiMocks.getAgentMemoDraft.mockResolvedValue(inReview);
    render(<AgentMemoPanel workspaceId="w1" />);
    expect(await screen.findByText("Business overview")).toBeDefined();
    expect(screen.getByText(/approximately 14 percent of revenue/)).toBeDefined();
    expect(screen.getByText("Financial performance")).toBeDefined();
    // The withheld section names its exact violations and serves no prose.
    expect(screen.getByText("Withheld by the grounding gate")).toBeDefined();
    expect(screen.getByText(/37%/)).toBeDefined();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDefined();
    expect(screen.getByText(/Draft version 1/)).toBeDefined();
  });

  it("accepting a section calls the decide API and renders the assembled draft", async () => {
    apiMocks.getAgentMemoDraft.mockResolvedValue(inReview);
    apiMocks.decideAgentMemoSection.mockResolvedValue(decided);
    render(<AgentMemoPanel workspaceId="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(apiMocks.decideAgentMemoSection).toHaveBeenCalledWith(
        "w1",
        "draft-artifact-1",
        "Business overview",
        "accept"
      )
    );
    expect(await screen.findByText("Assembled memo draft")).toBeDefined();
    expect(screen.getByText(/## Business overview/)).toBeDefined();
    expect(screen.getByText("accepted by analyst-1")).toBeDefined();
    // The decided section no longer offers Accept/Reject.
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("explains a not_run gating honestly instead of rendering empty sections", async () => {
    apiMocks.getAgentMemoDraft.mockResolvedValue(null);
    apiMocks.runAgentMemoDraft.mockResolvedValue({
      workspace_id: "w1",
      status: "not_run",
      reason: "mock",
      sections: [],
      generated_at: "2026-07-18T12:00:00Z",
      draft_artifact_id: null,
      version: null,
      assembled_markdown: null,
    } satisfies AgentMemoDraft);
    render(<AgentMemoPanel workspaceId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: "Draft memo sections" }));
    expect(await screen.findByText("Memo draft did not run")).toBeDefined();
    expect(screen.getByText(/deterministic mock mode/)).toBeDefined();
  });
});
