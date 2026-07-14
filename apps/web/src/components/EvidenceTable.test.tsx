import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceTable } from "./EvidenceTable";
import type { Evidence } from "@/lib/types";

const evidence: Evidence = {
  id: "evidence-1",
  workspace_id: "workspace-1",
  ref: "E-001",
  claim: "Revenue grew 12%.",
  claim_type: "fact",
  source_name: "Management accounts",
  source_type: "financials",
  source_url: null,
  source_date: "2026-06-30",
  source_section: "Revenue bridge",
  evidence_text: "FY26 revenue was $112 million versus $100 million in FY25.",
  confidence: 0.92,
  agent_name: "financial-analyst",
  created_at: "2026-07-01T12:00:00Z",
};

describe("EvidenceTable", () => {
  it("reveals the retained evidence text on demand", () => {
    render(<EvidenceTable evidence={[evidence]} workspaceId="workspace-1" />);
    expect(screen.queryByText(evidence.evidence_text)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View excerpt" }));
    expect(screen.getByText(evidence.evidence_text)).toBeVisible();
    expect(screen.getByRole("button", { name: "Hide excerpt" })).toHaveAttribute("aria-expanded", "true");
  });
});
