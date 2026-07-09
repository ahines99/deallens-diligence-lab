import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { DataTable, type Column } from "@/components/ui/Table";
import { TrendChart } from "@/components/TrendChart";
import { formatPct, formatUsd } from "@/lib/formatting";
import type { FinancialTrends, TrendPoint } from "@/lib/types";

const ROW_COLUMNS: Column<TrendPoint>[] = [
  {
    key: "year",
    header: "Year",
    render: (r) => <span className="font-medium text-slate-800">{r.year}</span>,
  },
  {
    key: "revenue",
    header: "Revenue",
    align: "right",
    render: (r) => <span className="tabular-nums text-slate-700">{formatUsd(r.revenue)}</span>,
  },
  {
    key: "gross_margin",
    header: "Gross margin",
    align: "right",
    render: (r) => <span className="tabular-nums text-slate-700">{formatPct(r.gross_margin)}</span>,
  },
  {
    key: "operating_margin",
    header: "Op. margin",
    align: "right",
    render: (r) => (
      <span className="tabular-nums text-slate-700">{formatPct(r.operating_margin)}</span>
    ),
  },
  {
    key: "net_margin",
    header: "Net margin",
    align: "right",
    render: (r) => <span className="tabular-nums text-slate-700">{formatPct(r.net_margin)}</span>,
  },
  {
    key: "rnd_pct",
    header: "R&D %",
    align: "right",
    render: (r) => <span className="tabular-nums text-slate-700">{formatPct(r.rnd_pct)}</span>,
  },
];

export default async function TrendsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let trends: FinancialTrends | null = null;
  let error: string | null = null;
  try {
    trends = await api.getTrends(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load financial trends.";
  }

  const yearRange =
    trends && trends.years.length > 0
      ? `${trends.years[0]}–${trends.years[trends.years.length - 1]}`
      : undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Financial trends"
        subtitle="Multi-year revenue and margin history reconstructed from SEC XBRL company facts."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : !trends ? (
        <EmptyState
          title="No multi-year trend data"
          description="Trends come from XBRL when a company is ingested by ticker."
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatTile
              label="Revenue CAGR"
              value={formatPct(trends.revenue_cagr, 1)}
              sub={yearRange ? `${yearRange} compound annual growth` : undefined}
              tone="indigo"
            />
            <StatTile label="Years covered" value={String(trends.rows.length)} sub={yearRange} />
            <StatTile label="Company" value={trends.target_name} />
          </div>

          <Card title="Revenue & margins" subtitle={yearRange}>
            <TrendChart rows={trends.rows} />
          </Card>

          <Card title="Year-by-year detail">
            <DataTable
              columns={ROW_COLUMNS}
              rows={trends.rows}
              getRowKey={(r) => r.year}
              empty="No annual rows available."
            />
          </Card>

          <Callout tone="info">
            Multi-year figures from SEC XBRL company facts. Fiscal-year gaps can occur when a
            year&apos;s XBRL frame doesn&apos;t normalize.
          </Callout>
        </>
      )}
    </div>
  );
}
