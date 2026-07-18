import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ModelQuality } from "@/lib/types";
import QualityPage from "./page";

const apiMocks = vi.hoisted(() => ({
  getModelQuality: vi.fn(),
}));

vi.mock("@/lib/serverApi", () => ({
  api: apiMocks,
  // The real helper's semantics matter here: an outage must surface, not read as clean data.
  loadOrUnavailable: async <T,>(promise: Promise<T>, fallback: T) => {
    try {
      return { data: await promise, unavailable: false };
    } catch {
      return { data: fallback, unavailable: true };
    }
  },
}));

const quality: ModelQuality = {
  generated_at: "2026-07-17T12:00:00Z",
  judge_evals: {
    status: "available",
    note: null,
    total: 12,
    faithful: 11,
    faithful_rate: 0.9167,
    groups: [
      {
        model_version: "claude-opus-4-8",
        prompt_version: "grounded-synth-v1",
        count: 12,
        faithful: 11,
        faithful_rate: 0.9167,
        mean_score: 0.94,
      },
    ],
  },
  retrieval_metrics: {
    status: "available",
    note: null,
    num_questions: 13,
    recall_ks: [1, 3, 5],
    rankers: { hybrid: { "recall@1": 0.769, "recall@3": 0.923, "recall@5": 1.0, mrr: 0.859 } },
  },
  calibration: {
    status: "available",
    note: null,
    partial_coverage_threshold: 0.5,
    abstain_coverage: 0.0,
    study: "src/eval/calibration_study.md",
  },
  prompts: {
    status: "available",
    note: null,
    prompts: [
      {
        prompt_id: "risk_extraction",
        prompt_version: "risk-extract-v1",
        prompt_hash: "a".repeat(64),
        model: "claude-opus-4-8",
      },
    ],
  },
  extraction_comparison: {
    status: "unavailable",
    note: "no extraction comparison has been run yet",
  },
  prompt_ab: {
    status: "available",
    note: null,
    reports: [
      {
        status: "completed",
        prompt_id: "grounded_synthesis",
        judge: "mock-faithfulness-v1",
        a: {
          prompt_version: "grounded-synth-v1",
          prompt_hash: "b".repeat(64),
          faithful: 9,
          faithful_rate: 0.9,
          judged: 10,
        },
        b: { prompt_hash_candidate: "c".repeat(64), faithful: 6, faithful_rate: 0.6, judged: 10 },
        winner: "a",
        generated_at: "2026-07-18T00:00:00Z",
      },
    ],
  },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("QualityPage", () => {
  it("renders each section with its own status and never fabricates zeros", async () => {
    apiMocks.getModelQuality.mockResolvedValue(quality);
    render(await QualityPage());

    expect(screen.getByText("Faithfulness evaluations")).toBeDefined();
    expect(screen.getByText("hybrid")).toBeDefined();
    expect(screen.getByText("risk_extraction")).toBeDefined();
    // The unavailable section shows its explanatory note, not empty data.
    expect(screen.getByText("no extraction comparison has been run yet")).toBeDefined();
    expect(screen.getByText(/winner: a/)).toBeDefined();
    expect(screen.getAllByText("unavailable").length).toBe(1);
    expect(screen.getAllByText("available").length).toBe(5);
  });

  it("renders an API outage as an explicit warning, never a clean empty dashboard", async () => {
    apiMocks.getModelQuality.mockRejectedValue(new Error("api down"));
    render(await QualityPage());
    expect(screen.getByText("Quality data unavailable")).toBeDefined();
    expect(screen.queryByText("Faithfulness evaluations")).toBeNull();
  });
});
