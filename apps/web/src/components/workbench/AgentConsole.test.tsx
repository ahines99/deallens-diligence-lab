import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentRun } from "@/lib/types";
import { AgentConsole } from "./AgentConsole";

const apiMocks = vi.hoisted(() => ({
  runDiligenceAgent: vi.fn(),
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

const base: AgentRun = {
  workspace_id: "w1",
  objective: "How concentrated is customer revenue?",
  status: "completed",
  reason: "applied",
  answer: "The largest customer represents approximately 14 percent of revenue.",
  steps: [
    {
      tool: "search_filings",
      arguments: { query: "customer concentration" },
      ok: true,
      result: { results: [{ section: "Item 1A", quote: "…14 percent…" }] },
      error: null,
    },
  ],
  tools_used: ["search_filings"],
  steps_used: 1,
  artifact_version_id: "artifact123456",
  manifest: {
    prompt_id: "diligence_agent",
    prompt_version: "diligence-agent-v1",
    prompt_hash: "a".repeat(64),
    model: "claude-opus-4-8",
  },
  grounding: { grounded: true, numeric_violations: [], unknown_refs: [] },
  generated_at: "2026-07-17T12:00:00Z",
};

async function runWith(run: AgentRun) {
  apiMocks.runDiligenceAgent.mockResolvedValue(run);
  render(<AgentConsole workspaceId="w1" />);
  fireEvent.change(screen.getByPlaceholderText(/Summarize the top three risks/), {
    target: { value: "How concentrated is customer revenue?" },
  });
  fireEvent.submit(screen.getByRole("button", { name: /Run diligence agent/ }).closest("form")!);
  await waitFor(() => expect(apiMocks.runDiligenceAgent).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentConsole", () => {
  it("renders a completed run with the tool transcript and grounding provenance", async () => {
    await runWith(base);
    expect(await screen.findByText(/approximately 14 percent of revenue/)).toBeDefined();
    expect(screen.getByText("search_filings")).toBeDefined();
    expect(screen.getByText("Grounding gate passed")).toBeDefined();
    expect(screen.getByText(/Sealed artifact/)).toBeDefined();
  });

  it("shows a withheld answer with the exact ungrounded tokens, never the rejected prose", async () => {
    await runWith({
      ...base,
      status: "rejected_ungrounded",
      reason: "grounding_failed",
      answer: null,
      grounding: { grounded: false, numeric_violations: ["23%"], unknown_refs: [] },
    });
    expect(await screen.findByText(/failed the grounding gate/)).toBeDefined();
    expect(screen.getByText(/23%/)).toBeDefined();
    expect(screen.queryByText(/approximately 14 percent of revenue/)).toBeNull();
  });

  it("explains a not_run gating instead of rendering an empty transcript", async () => {
    await runWith({
      ...base,
      status: "not_run",
      reason: "mock",
      answer: null,
      steps: [],
      steps_used: 0,
      artifact_version_id: null,
      manifest: null,
      grounding: null,
    });
    expect(await screen.findByText("Agent did not run")).toBeDefined();
    expect(screen.getByText(/deterministic mock mode/)).toBeDefined();
  });
});
