import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventTimeline } from "./EventTimeline";
import { FilingWatchPanel } from "./FilingWatchPanel";
import { InsiderView } from "./InsiderView";
import { NewsFeed } from "./NewsFeed";
import { ThemeScanPanel } from "./ThemeScanPanel";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(cleanup);

describe("external signal source availability", () => {
  it("does not translate unavailable news or event sources into clean empty results", () => {
    const { rerender } = render(<NewsFeed data={{
      workspace_id: "w1", query: "Target", articles: [], generated_at: "2026-07-13T00:00:00Z",
      source_status: "unavailable", source_error: "GDELT timed out.",
    }} />);
    expect(screen.getByText("GDELT timed out.")).toBeInTheDocument();
    expect(screen.queryByText("No recent articles found.")).not.toBeInTheDocument();

    rerender(<EventTimeline data={{
      workspace_id: "w1", events: [], generated_at: "2026-07-13T00:00:00Z",
      source_status: "unavailable", source_error: "SEC unavailable.",
    }} />);
    expect(screen.getByText("SEC unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("No filing events found.")).not.toBeInTheDocument();
  });

  it("renders unknown insider and theme counts as unavailable, never zero", () => {
    const { rerender } = render(<InsiderView data={{
      workspace_id: "w1", transactions: [], generated_at: "2026-07-13T00:00:00Z",
      summary: { buys: null, sells: null, net_shares: null, window_days: 90 },
      source_status: "unavailable", source_error: "Form 4 source unavailable.",
    }} />);
    expect(screen.getByText("Form 4 source unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("No insider transactions in the window.")).not.toBeInTheDocument();

    rerender(<ThemeScanPanel data={{
      workspace_id: "w1", generated_at: "2026-07-13T00:00:00Z",
      themes: [{ theme: "litigation", label: "Litigation", count: null, hits: [] }],
      source_status: "unavailable", source_error: "Search source unavailable.",
    }} />);
    expect(screen.getByText("Coverage incomplete")).toBeInTheDocument();
    expect(screen.queryByText("0 hits")).not.toBeInTheDocument();
    expect(screen.queryByText("No matches")).not.toBeInTheDocument();
  });

  it("does not label an unknown filing-watch result as up to date", () => {
    render(<FilingWatchPanel workspaceId="w1" initial={{
      workspace_id: "w1", last_ingested_date: null, has_new: null, new_filings: [],
      source_status: "unavailable", source_error: "Filing watch unavailable.",
      generated_at: "2026-07-13T00:00:00Z",
    }} />);
    expect(screen.getByText("Filing watch unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Up to date")).not.toBeInTheDocument();
    expect(screen.queryByText("No new filings since the last ingestion.")).not.toBeInTheDocument();
  });
});
