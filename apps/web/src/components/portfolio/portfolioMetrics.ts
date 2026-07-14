import type {
  PortfolioDealReturnsSnapshot,
  PortfolioDealRow,
  PortfolioReturnCase,
} from "@/lib/types";

export interface PortfolioSourceRollup {
  totalSources: number;
  readySources: number;
  partialSources: number;
  failedSources: number;
  staleWorkspaces: number;
  workspacesWithoutSources: number;
}

export interface PortfolioFinancialRollup {
  mappedFacts: number;
  totalFacts: number;
  mappingCoverage: number | null;
  reconciliationsPassed: number;
  reconciliationsTotal: number;
  reconciliationScore: number | null;
  openExceptions: number;
  inconsistentPeriods: number;
}

export function buildSourceRollup(deals: PortfolioDealRow[]): PortfolioSourceRollup {
  return deals.reduce<PortfolioSourceRollup>(
    (summary, deal) => {
      summary.totalSources += deal.source_health.total_sources;
      summary.readySources += deal.source_health.ready;
      summary.partialSources += deal.source_health.partial;
      summary.failedSources += deal.source_health.failed;
      summary.staleWorkspaces += Number(deal.source_health.stale);
      summary.workspacesWithoutSources += Number(deal.source_health.total_sources === 0);
      return summary;
    },
    {
      totalSources: 0,
      readySources: 0,
      partialSources: 0,
      failedSources: 0,
      staleWorkspaces: 0,
      workspacesWithoutSources: 0,
    },
  );
}

export function buildFinancialRollup(deals: PortfolioDealRow[]): PortfolioFinancialRollup {
  const totals = deals.reduce(
    (summary, deal) => {
      const quality = deal.financial_quality;
      summary.mappedFacts += quality.mapped_facts;
      summary.totalFacts += quality.total_facts;
      summary.reconciliationsPassed += quality.reconciliations_passed;
      summary.reconciliationsTotal += quality.reconciliations_total;
      summary.openExceptions += quality.open_exceptions;
      summary.inconsistentPeriods += Number(quality.period_consistent === false);
      return summary;
    },
    {
      mappedFacts: 0,
      totalFacts: 0,
      reconciliationsPassed: 0,
      reconciliationsTotal: 0,
      openExceptions: 0,
      inconsistentPeriods: 0,
    },
  );

  return {
    ...totals,
    mappingCoverage: totals.totalFacts
      ? Math.round((totals.mappedFacts / totals.totalFacts) * 1000) / 10
      : null,
    reconciliationScore: totals.reconciliationsTotal
      ? Math.round((totals.reconciliationsPassed / totals.reconciliationsTotal) * 1000) / 10
      : null,
  };
}

export function getReturnCase(
  snapshot: PortfolioDealReturnsSnapshot,
  caseKey: string,
): PortfolioReturnCase | null {
  return snapshot.cases.find((item) => item.case_key === caseKey) ?? null;
}

export function readinessBand(score: number) {
  if (score >= 80) return { label: "IC ready", tone: "green" as const };
  if (score >= 60) return { label: "Advancing", tone: "indigo" as const };
  if (score >= 40) return { label: "Developing", tone: "amber" as const };
  return { label: "Early", tone: "red" as const };
}
