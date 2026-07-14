import { describe, expect, it } from "vitest";
import type { UnderwritingCaseVersion } from "@/lib/types";
import { savedUnderwritingControlValues } from "./underwritingHydration";

function version(case_key: "base" | "upside" | "downside", growth: number, margin: number, exit: number): UnderwritingCaseVersion {
  return {
    case_key,
    assumptions: {
      transaction: { exit_multiple: exit },
      projection: { default_drivers: { annual_revenue_growth: growth, ebitda_margin: margin } },
      debt_tranches: case_key === "base" ? [{ tranche_type: "revolver", spread: 0.0625 }] : [],
      covenants: case_key === "base" ? [
        { metric: "total_leverage", test: "maximum", threshold: 5.25 },
        { metric: "interest_coverage", test: "minimum", threshold: 2.1 },
      ] : [],
    },
  } as UnderwritingCaseVersion;
}

describe("saved underwriting form hydration", () => {
  it("round-trips custom scenario deltas, revolver spread, and covenant controls", () => {
    const values = savedUnderwritingControlValues([
      version("base", 0.08, 0.2, 10),
      version("upside", 0.115, 0.225, 10.8),
      version("downside", 0.035, 0.145, 8.6),
    ]);
    expect(values).toMatchObject({
      revolver_spread: "6.25",
      max_leverage: "5.25",
      min_interest_coverage: "2.1",
      up_growth_delta: "3.5",
      up_margin_delta: "2.5",
      up_exit_delta: "0.8",
      down_growth_delta: "-4.5",
      down_margin_delta: "-5.5",
      down_exit_delta: "-1.4",
    });
  });
});
