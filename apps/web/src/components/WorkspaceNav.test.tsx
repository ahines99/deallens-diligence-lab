import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceNav } from "./WorkspaceNav";

vi.mock("next/navigation", () => ({ usePathname: () => "/workspaces/demo/events" }));

describe("WorkspaceNav", () => {
  it("keeps all public-signal and evidence routes discoverable", () => {
    render(<WorkspaceNav base="/workspaces/demo" />);
    for (const label of [
      "Filing events",
      "Insider activity",
      "News signals",
      "Macro overlay",
      "GovCon exposure",
      "QoE forensics",
      "Diligence questions",
      "Red-team case",
      "Evidence trail",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });
});
