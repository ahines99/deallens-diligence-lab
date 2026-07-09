import { Callout } from "@/components/ui/Callout";
import { DataTable, type Column } from "@/components/ui/Table";
import { formatPct, formatUsd } from "@/lib/formatting";
import type { ComparableCompany } from "@/lib/types";

const COLUMNS: Column<ComparableCompany>[] = [
  {
    key: "ticker",
    header: "Ticker",
    render: (c) => <span className="font-mono text-xs font-semibold text-ink">{c.ticker}</span>,
  },
  {
    key: "company_name",
    header: "Company",
    render: (c) => <span className="font-medium text-ink">{c.company_name}</span>,
  },
  {
    key: "sector",
    header: "Sector",
    render: (c) => <span className="text-muted">{c.sector}</span>,
  },
  {
    key: "revenue",
    header: "Revenue",
    align: "right",
    render: (c) => <span className="tabular-nums text-body">{formatUsd(c.revenue)}</span>,
  },
  {
    key: "revenue_growth",
    header: "Rev. growth",
    align: "right",
    render: (c) => <span className="tabular-nums text-body">{formatPct(c.revenue_growth)}</span>,
  },
  {
    key: "gross_margin",
    header: "Gross margin",
    align: "right",
    render: (c) => <span className="tabular-nums text-body">{formatPct(c.gross_margin)}</span>,
  },
  {
    key: "operating_margin",
    header: "Op. margin",
    align: "right",
    render: (c) => <span className="tabular-nums text-body">{formatPct(c.operating_margin)}</span>,
  },
  {
    key: "net_margin",
    header: "Net margin",
    align: "right",
    render: (c) => <span className="tabular-nums text-body">{formatPct(c.net_margin)}</span>,
  },
  {
    key: "rnd_pct",
    header: "R&D %",
    align: "right",
    render: (c) => <span className="tabular-nums text-body">{formatPct(c.rnd_pct)}</span>,
  },
  {
    key: "notes",
    header: "Notes",
    render: (c) => <span className="block max-w-xs text-xs text-muted">{c.notes || "—"}</span>,
  },
];

export function CompsTable({ comps }: { comps: ComparableCompany[] }) {
  return (
    <div className="space-y-4">
      <Callout tone="info">
        Peers are real public companies; financials are from SEC XBRL. Market multiples (EV/Revenue,
        market cap) are intentionally omitted — no free market-data source.
      </Callout>
      <DataTable
        columns={COLUMNS}
        rows={comps}
        getRowKey={(c) => c.id}
        empty="No comparable companies yet."
      />
    </div>
  );
}

export default CompsTable;
