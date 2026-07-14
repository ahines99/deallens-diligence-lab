import type { CaseKey, UnderwritingCaseVersion } from "@/lib/types";

function caseFor(cases: UnderwritingCaseVersion[], key: CaseKey) {
  return cases.find((item) => item.case_key === key)?.assumptions;
}

export function savedUnderwritingControlValues(cases: UnderwritingCaseVersion[]) {
  const base = caseFor(cases, "base");
  if (!base) return {};
  const upside = caseFor(cases, "upside");
  const downside = caseFor(cases, "downside");
  const baseDrivers = base.projection.default_drivers;
  const clean = (value: number) => String(Number(value.toFixed(6)));
  const deltaPercent = (value: number | undefined, baseValue: number, fallback: number) =>
    clean(value === undefined ? fallback : (value - baseValue) * 100);
  const exitDelta = (value: number | undefined, fallback: number) =>
    clean(value === undefined ? fallback : value - base.transaction.exit_multiple);
  const revolver = base.debt_tranches.find((item) => item.tranche_type === "revolver");
  const maxLeverage = base.covenants.find((item) => item.metric === "total_leverage" && item.test === "maximum");
  const minCoverage = base.covenants.find((item) => item.metric === "interest_coverage" && item.test === "minimum");

  return {
    revolver_spread: clean((revolver?.spread ?? 0.045) * 100),
    max_leverage: String(maxLeverage?.threshold ?? 6),
    min_interest_coverage: String(minCoverage?.threshold ?? 1.5),
    up_growth_delta: deltaPercent(upside?.projection.default_drivers.annual_revenue_growth, baseDrivers.annual_revenue_growth, 2),
    up_margin_delta: deltaPercent(upside?.projection.default_drivers.ebitda_margin, baseDrivers.ebitda_margin, 2),
    up_exit_delta: exitDelta(upside?.transaction.exit_multiple, 0.5),
    down_growth_delta: deltaPercent(downside?.projection.default_drivers.annual_revenue_growth, baseDrivers.annual_revenue_growth, -3),
    down_margin_delta: deltaPercent(downside?.projection.default_drivers.ebitda_margin, baseDrivers.ebitda_margin, -3),
    down_exit_delta: exitDelta(downside?.transaction.exit_multiple, -1),
  };
}
