import { Badge } from "@/components/ui/Badge";
import { DataTable, type Column } from "@/components/ui/Table";
import { formatDate, formatNumber } from "@/lib/formatting";
import type { Filing } from "@/lib/types";

const COLUMNS: Column<Filing>[] = [
  {
    key: "form_type",
    header: "Form",
    render: (f) => <span className="font-mono text-xs font-medium text-slate-800">{f.form_type}</span>,
  },
  {
    key: "company_name",
    header: "Company",
    render: (f) => (
      <div className="min-w-0">
        <div className="font-medium text-slate-800">{f.company_name}</div>
        {f.ticker && <div className="text-xs text-slate-400">{f.ticker}</div>}
      </div>
    ),
  },
  {
    key: "filing_date",
    header: "Filed",
    render: (f) => <span className="tabular-nums text-slate-600">{formatDate(f.filing_date)}</span>,
  },
  {
    key: "section_count",
    header: "Sections",
    align: "right",
    render: (f) => <span className="tabular-nums text-slate-600">{formatNumber(f.section_count)}</span>,
  },
  {
    key: "source",
    header: "Source",
    render: (f) =>
      f.is_synthetic ? <Badge tone="amber">Synthetic</Badge> : <Badge tone="green">Live SEC</Badge>,
  },
  {
    key: "document",
    header: "Document",
    render: (f) =>
      f.document_url ? (
        <a
          href={f.document_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-brand-700 underline underline-offset-2 hover:text-brand-600"
        >
          View
        </a>
      ) : (
        <span className="text-slate-400">—</span>
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
