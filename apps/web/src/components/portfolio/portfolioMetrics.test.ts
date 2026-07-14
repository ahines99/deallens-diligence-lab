import { describe, expect, it } from "vitest";
import type { PortfolioDealReturnsSnapshot, PortfolioDealRow } from "@/lib/types";
import {
  buildFinancialRollup,
  buildSourceRollup,
  getReturnCase,
  readinessBand,
} from "./portfolioMetrics";

function deal(
  overrides: {
    sources?: Partial<PortfolioDealRow["source_health"]>;
    quality?: Partial<PortfolioDealRow["financial_quality"]>;
  } = {},
) {
  return {
    source_health: {
      status: "ready",
      total_sources: 2,
      ready: 2,
      partial: 0,
      failed: 0,
      freshest_at: "2026-07-13T12:00:00Z",
      oldest_age_days: 5,
      stale: false,
      ...overrides.sources,
    },
    financial_quality: {
      mapping_coverage: 80,
      mapped_facts: 8,
      total_facts: 10,
      reconciliation_score: 50,
      reconciliations_passed: 1,
      reconciliations_total: 2,
      open_exceptions: 1,
      qoe_adjustment_amount: 0,
      qoe_materiality: null,
      reported_ebitda: null,
      sponsor_adjusted_ebitda: null,
      ebitda_variance: null,
      period_consistent: true,
      period_diagnostics: [],
      ...overrides.quality,
    },
  } as PortfolioDealRow;
}

describe("portfolio metric rollups", () => {
  it("aggregates source states without treating an unconfigured workspace as ready", () => {
    const result = buildSourceRollup([
      deal(),
      deal({
        sources: {
          status: "not_configured",
          total_sources: 0,
          ready: 0,
          stale: true,
        },
      }),
    ]);

    expect(result).toEqual({
      totalSources: 2,
      readySources: 2,
      partialSources: 0,
      failedSources: 0,
      staleWorkspaces: 1,
      workspacesWithoutSources: 1,
    });
  });

  it("weights coverage by underlying facts and reconciliations", () => {
    const result = buildFinancialRollup([
      deal(),
      deal({
        quality: {
          mapped_facts: 1,
          total_facts: 2,
          reconciliations_passed: 3,
          reconciliations_total: 3,
          open_exceptions: 2,
          period_consistent: false,
        },
      }),
    ]);

    expect(result.mappingCoverage).toBe(75);
    expect(result.reconciliationScore).toBe(80);
    expect(result.openExceptions).toBe(3);
    expect(result.inconsistentPeriods).toBe(1);
  });

  it("returns null coverage when there is no source financial population", () => {
    const result = buildFinancialRollup([]);
    expect(result.mappingCoverage).toBeNull();
    expect(result.reconciliationScore).toBeNull();
  });
});

describe("portfolio return and readiness selectors", () => {
  it("does not invent a missing underwriting case", () => {
    const snapshot = {
      cases: [{ case_key: "base", moic: 2.1 }],
    } as PortfolioDealReturnsSnapshot;
    expect(getReturnCase(snapshot, "base")?.moic).toBe(2.1);
    expect(getReturnCase(snapshot, "downside")).toBeNull();
  });

  it("uses explicit readiness thresholds", () => {
    expect(readinessBand(80).label).toBe("IC ready");
    expect(readinessBand(60).label).toBe("Advancing");
    expect(readinessBand(40).label).toBe("Developing");
    expect(readinessBand(39.9).label).toBe("Early");
  });
});
