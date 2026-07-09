import { Card } from "@/components/ui/Card";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { StatTile, type StatTone } from "@/components/ui/StatTile";
import { DataTable, type Column } from "@/components/ui/Table";
import { formatUsd, formatNumber, formatDate } from "@/lib/formatting";
import type { InsiderActivity, InsiderTx } from "@/lib/types";

const TYPE_TONE: Record<InsiderTx["type"], BadgeTone> = {
  buy: "green",
  sell: "red",
  other: "slate",
};

const columns: Column<InsiderTx>[] = [
  { key: "date", header: "Date", render: (t) => <span className="tabular-nums">{formatDate(t.date)}</span> },
  { key: "insider", header: "Insider", render: (t) => <span className="font-medium text-ink">{t.insider || "—"}</span> },
  { key: "role", header: "Role", render: (t) => <span className="text-muted">{t.role || "—"}</span> },
  {
    key: "type",
    header: "Type",
    render: (t) => <Badge tone={TYPE_TONE[t.type]}>{t.type}</Badge>,
  },
  { key: "shares", header: "Shares", align: "right", render: (t) => <span className="tabular-nums">{formatNumber(t.shares)}</span> },
  { key: "price", header: "Price", align: "right", render: (t) => <span className="tabular-nums">{t.price === null ? "—" : `$${t.price.toFixed(2)}`}</span> },
  { key: "value", header: "Value", align: "right", render: (t) => <span className="tabular-nums">{t.value === null ? "—" : formatUsd(t.value)}</span> },
  {
    key: "link",
    header: "",
    render: (t) =>
      t.url ? (
        <a href={t.url} target="_blank" rel="noopener noreferrer" className="text-2xs font-semibold uppercase tracking-eyebrow text-accent hover:underline">
          Form 4 ↗
        </a>
      ) : null,
  },
];

export function InsiderView({ data }: { data: InsiderActivity }) {
  const { summary } = data;
  const netTone: StatTone =
    summary.net_shares === null ? "default" : summary.net_shares >= 0 ? "positive" : "negative";
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line shadow-panel sm:grid-cols-4">
        <div className="bg-panel px-4 py-4">
          <StatTile label="Buys" value={summary.buys} tone={summary.buys > 0 ? "positive" : "default"} />
        </div>
        <div className="bg-panel px-4 py-4">
          <StatTile label="Sells" value={summary.sells} tone={summary.sells > 0 ? "negative" : "default"} />
        </div>
        <div className="bg-panel px-4 py-4">
          <StatTile
            label="Net shares"
            value={summary.net_shares === null ? "n/a" : formatNumber(summary.net_shares)}
            tone={netTone}
          />
        </div>
        <div className="bg-panel px-4 py-4">
          <StatTile label="Window" value={`${summary.window_days}d`} sub="lookback" />
        </div>
      </div>

      <Card title="Transactions" subtitle="Parsed from SEC Form 4 ownership filings">
        <DataTable columns={columns} rows={data.transactions} empty="No insider transactions in the window." />
      </Card>
    </div>
  );
}

export default InsiderView;
