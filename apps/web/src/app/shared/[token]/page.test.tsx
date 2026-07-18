import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SharedWorkspaceSnapshot } from "@/lib/types";
import SharedSnapshotPage from "./page";

const apiMocks = vi.hoisted(() => ({
  getSharedSnapshot: vi.fn(),
}));

const MockApiError = vi.hoisted(
  () =>
    class MockApiError extends Error {
      status: number;
      constructor(status: number, message: string) {
        super(message);
        this.status = status;
      }
    },
);

vi.mock("@/lib/serverApi", () => ({
  api: apiMocks,
  ApiError: MockApiError,
}));

const notFound = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ notFound }));

const snapshot: SharedWorkspaceSnapshot = {
  scope: "read_only",
  workspace: {
    name: "Project Atlas",
    deal_type: "buyout",
    status: "complete",
    investment_question: "Is Atlas attractive?",
  },
  target: {
    name: "Atlas Corp",
    ticker: "ATLS",
    sector: "Software",
    description: "Vertical SaaS for logistics.",
    target_type: "public_company",
  },
  risks: [
    {
      title: "Top-5 customers are 60% of revenue",
      category: "customer_concentration",
      category_label: "Customer concentration",
      severity: "high",
      severity_score: 8,
    },
  ],
  counts: { risks: 1 },
  disclaimer: "Read-only shared snapshot. Not investment advice.",
  watermark: "Shared read-only · Atlas Capital · link ab12cd34 · 2026-07-18",
};

const renderPage = async () =>
  render(await SharedSnapshotPage({ params: Promise.resolve({ token: "dsh_test" }) }));

describe("SharedSnapshotPage (public share-token view)", () => {
  afterEach(cleanup);

  it("renders the snapshot with the server-composed watermark banner and tiled overlay", async () => {
    apiMocks.getSharedSnapshot.mockResolvedValue(snapshot);
    await renderPage();

    // The watermark is the SERVER-composed payload string, rendered verbatim and visibly.
    expect(screen.getByText(snapshot.watermark)).toBeInTheDocument();
    // The persistent tiled overlay carries the same text in its SVG background.
    const overlay = screen.getByTestId("share-watermark-overlay");
    expect(overlay.style.backgroundImage).toContain(encodeURIComponent("Atlas Capital"));
    expect(overlay.style.backgroundImage).toContain(encodeURIComponent("ab12cd34"));

    // The snapshot itself renders.
    expect(screen.getByText("Project Atlas")).toBeInTheDocument();
    expect(screen.getByText("Atlas Corp")).toBeInTheDocument();
    expect(screen.getByText("Top-5 customers are 60% of revenue")).toBeInTheDocument();
    expect(screen.getByText(snapshot.disclaimer)).toBeInTheDocument();
    expect(notFound).not.toHaveBeenCalled();
  });

  it("shows the revoked/expired state on 410 without rendering any snapshot content", async () => {
    apiMocks.getSharedSnapshot.mockRejectedValue(new MockApiError(410, "revoked"));
    await renderPage();

    expect(screen.getByText("Share link no longer active")).toBeInTheDocument();
    expect(screen.queryByTestId("share-watermark-overlay")).not.toBeInTheDocument();
    expect(screen.queryByText("Project Atlas")).not.toBeInTheDocument();
  });
});
