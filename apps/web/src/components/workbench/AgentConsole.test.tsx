import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentRun } from "@/lib/types";
import { AgentConsole } from "./AgentConsole";

const apiMocks = vi.hoisted(() => ({
  runDiligenceAgent: vi.fn(),
  listAgentRuns: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  API_BASE: "/backend",
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

function submitObjective() {
  render(<AgentConsole workspaceId="w1" />);
  fireEvent.change(screen.getByPlaceholderText(/Summarize the top three risks/), {
    target: { value: "How concentrated is customer revenue?" },
  });
  fireEvent.submit(screen.getByRole("button", { name: /Run diligence agent/ }).closest("form")!);
}

/** Legacy path: streaming is unavailable (fetch rejects), so the console falls back to the
 * non-streaming endpoint exactly once. */
async function runWith(run: AgentRun) {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("streaming unavailable")));
  apiMocks.runDiligenceAgent.mockResolvedValue(run);
  submitObjective();
  await waitFor(() => expect(apiMocks.runDiligenceAgent).toHaveBeenCalled());
}

/** A controllable SSE response body for the streaming fetch mock. */
function sseResponse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const encoder = new TextEncoder();
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (event: string, data: unknown) =>
      controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    close: () => controller.close(),
    fail: (err: unknown) => controller.error(err),
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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

  it("streams tool steps into the live timeline, then renders the sealed record", async () => {
    const sse = sseResponse();
    const fetchMock = vi.fn().mockResolvedValue(sse.response);
    vi.stubGlobal("fetch", fetchMock);
    submitObjective();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/backend/api/workspaces/w1/agent/run-stream",
        expect.objectContaining({ method: "POST" }),
      ),
    );

    sse.push("started", { workspace_id: "w1", objective: base.objective });
    sse.push("tool_step", { step: base.steps[0], index: 0 });

    // The step renders live, before any finished frame or sealed record exists.
    expect(await screen.findByText("search_filings")).toBeDefined();
    expect(screen.getByText(/Tool timeline \(streaming\)/)).toBeDefined();
    expect(screen.queryByText(/Sealed artifact/)).toBeNull();

    sse.push("finished", base);
    sse.close();

    expect(await screen.findByText(/approximately 14 percent of revenue/)).toBeDefined();
    expect(screen.getByText(/Sealed artifact/)).toBeDefined();
    expect(screen.getByText("Grounding gate passed")).toBeDefined();
    // The live timeline is replaced by the sealed transcript once the run completes.
    expect(screen.queryByText(/Tool timeline \(streaming\)/)).toBeNull();
    // The streaming path never touches the non-streaming endpoint (no double-run).
    expect(apiMocks.runDiligenceAgent).not.toHaveBeenCalled();
    expect(apiMocks.listAgentRuns).not.toHaveBeenCalled();
  });

  it("rehydrates its OWN sealed run by client_request_id when the stream drops mid-run", async () => {
    const sse = sseResponse();
    const fetchMock = vi.fn().mockResolvedValue(sse.response);
    vi.stubGlobal("fetch", fetchMock);
    // A newer, unrelated run sits at index 0: recovery must match by request id, never take
    // runs[0] (which races the end-of-run seal and can be a previous run entirely).
    apiMocks.listAgentRuns.mockImplementation(async () => {
      const body = JSON.parse(
        (fetchMock.mock.calls[0]![1] as RequestInit).body as string,
      ) as { client_request_id: string };
      return [
        { ...base, answer: "A previous unrelated run.", client_request_id: "prev-run-9999" },
        {
          ...base,
          answer: "Sealed answer reloaded from the transcript.",
          client_request_id: body.client_request_id,
        },
      ];
    });
    submitObjective();

    sse.push("started", { workspace_id: "w1", objective: base.objective });
    sse.push("tool_step", { step: base.steps[0], index: 0 });
    expect(await screen.findByText("search_filings")).toBeDefined();

    sse.fail(new Error("connection lost"));

    expect(
      await screen.findByText(/Sealed answer reloaded from the transcript/),
    ).toBeDefined();
    expect(screen.getByText(/stream dropped mid-run/)).toBeDefined();
    expect(screen.queryByText(/A previous unrelated run/)).toBeNull();
    expect(apiMocks.listAgentRuns).toHaveBeenCalledWith("w1");
    // The fallback never re-runs the agent after a mid-run drop.
    expect(apiMocks.runDiligenceAgent).not.toHaveBeenCalled();
  });

  it("carries one request id from the stream POST into the fallback POST", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("streaming unavailable"));
    vi.stubGlobal("fetch", fetchMock);
    apiMocks.runDiligenceAgent.mockResolvedValue(base);
    submitObjective();
    await waitFor(() => expect(apiMocks.runDiligenceAgent).toHaveBeenCalled());

    const streamBody = JSON.parse(
      (fetchMock.mock.calls[0]![1] as RequestInit).body as string,
    ) as { client_request_id: string };
    expect(streamBody.client_request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    // Same id on both POSTs — the server deduplicates, so one click can never run twice.
    expect(apiMocks.runDiligenceAgent).toHaveBeenCalledWith(
      "w1",
      "How concentrated is customer revenue?",
      8,
      streamBody.client_request_id,
    );
  });

  it("recovers the sealed run when the fallback POST is refused as a duplicate (409)", async () => {
    const { ApiError } = await import("@/lib/api");
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("connection reset"));
    vi.stubGlobal("fetch", fetchMock);
    apiMocks.runDiligenceAgent.mockRejectedValue(
      new ApiError(409, "duplicate_in_flight: a run with this client_request_id is still executing"),
    );
    apiMocks.listAgentRuns.mockImplementation(async () => {
      const body = JSON.parse(
        (fetchMock.mock.calls[0]![1] as RequestInit).body as string,
      ) as { client_request_id: string };
      return [
        {
          ...base,
          answer: "Sealed answer from the run the server refused to duplicate.",
          client_request_id: body.client_request_id,
        },
      ];
    });
    submitObjective();

    expect(
      await screen.findByText(/Sealed answer from the run the server refused to duplicate/),
    ).toBeDefined();
    expect(screen.getByText(/the run had already started/)).toBeDefined();
    // Exactly one fallback attempt — the 409 is resolved by recovery, never by re-POSTing.
    expect(apiMocks.runDiligenceAgent).toHaveBeenCalledTimes(1);
  });
});
