import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LboCalculator } from "./LboCalculator";

describe("LboCalculator disclosures", () => {
  it("describes the same debt-paydown mechanic used by the API model", () => {
    render(<LboCalculator workspaceId="workspace-1" />);
    expect(screen.getByText(/50% of projected EBITDA annual FCF proxy/i)).toBeVisible();
    expect(screen.queryByText(/debt is held flat/i)).not.toBeInTheDocument();
  });
});
