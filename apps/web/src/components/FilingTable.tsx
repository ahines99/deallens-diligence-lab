import { Badge } from "@/components/ui/Badge";
import { DataTable, type Column } from "@/components/ui/Table";
import { formatDate, formatNumber } from "@/lib/formatting";
import type { Filing } from "@/lib/types";

const COLUMNS: Column<Filing>[] = [
  {
    key: "form_type",
    header: "Form",
    render: (f) => <span className="font-mono text-xs font-medium text-ink">{f.form_type}</span>,
  },
  {
    key: "company_name",
    header: "Company",
    render: (f) => (
      <div className="min-w-0">
        <div className="font-medium text-ink">{f.company_name}</div>
        {f.ticker && <div className="text-2xs text-faint">{f.ticker}</div>}
      </div>
    ),
  },
  {
    key: "filing_date",
    header: "Filed",
    render: (f) => <span className="tabular-nums text-muted">{formatDate(f.filing_date)}</span>,
  },
  {
    key: "section_count",
    header: "Sections",
    align: "right",
    render: (f) => <span className="tabular-nums text-muted">{formatNumber(f.section_count)}</span>,
  },
  {
    key: "source",
    header: "Source",
    render: (f) =>
      f.is_synthetic ? <Badge tone="gold">Synthetic</Badge> : <Badge tone="green">Live SEC</Badge>,
  },
  {
    key: "document",
    header: "Document",
    align: "right",
    render: (f) =>
      f.document_url ? (
        <a
          href={f.document_url}
          target="_blank"
          rel="noopener noreferrer"
          className="whitespace-nowrap text-accent underline-offset-2 hover:underline"
        >
          View →
        </a>
      ) : (
        <span className="text-faint">—</span>
      ),
  },
];

export function FilingTable({ filings }: { filings: Filing[] }) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={filings}
      getRowKey={(f) => f.id}
      empty="No filings ingested yet."
    />
  );
}

export default FilingTable;
