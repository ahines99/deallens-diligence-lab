import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActorProvider } from "@/components/identity/ActorContext";
import { ApiError, api } from "@/lib/api";
import type { Deal, Fund, Organization, Workspace } from "@/lib/types";
import { PipelineBoard } from "./PipelineBoard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const organization: Organization = {
  id: "org-1", name: "Northbridge Capital", slug: "northbridge-capital",
  created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z",
} as Organization;

const fund: Fund = {
  id: "fund-1", organization_id: "org-1", name: "Fund IV", vintage_year: 2026,
  strategy: "buyout", base_currency: "USD",
  created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z",
} as Fund;

const deal: Deal = {
  id: "deal-1", organization_id: "org-1", fund_id: "fund-1", workspace_id: null,
  code: "PROJECT ATLAS", name: "Project Atlas", target_company: "Atlas Industrial",
  deal_type: "buyout", stage: "diligence", status: "active", owner_actor_id: null,
  ic_date: null, summary: "", version: 1,
  created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

function mockPipelineReads() {
  vi.spyOn(api, "listOrganizations").mockResolvedValue([organization]);
  vi.spyOn(api, "listWorkspaces").mockResolvedValue([] as Workspace[]);
  vi.spyOn(api, "listFunds").mockResolvedValue([fund]);
  vi.spyOn(api, "listDeals").mockResolvedValue([deal]);
}

describe("PipelineBoard stage transitions", () => {
  it("surfaces a stage-gate 409 instead of silently snapping back (audit H6)", async () => {
    mockPipelineReads();
    const detail = "Resolve required stage gates before advancing to IC Review";
    vi.spyOn(api, "transitionDeal").mockRejectedValue(new ApiError(409, detail));

    render(
      <ActorProvider>
        <PipelineBoard />
      </ActorProvider>,
    );
    await screen.findByText("Atlas Industrial");
    fireEvent.change(screen.getByLabelText("Change stage"), { target: { value: "ic_review" } });

    // The handler previously used try/finally with no catch: the rejection was
    // unhandled and the analyst got zero feedback about why the move reverted.
    await waitFor(() => expect(screen.getByText(detail)).toBeInTheDocument());
  });
});
