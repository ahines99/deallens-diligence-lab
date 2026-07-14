import { api } from "@/lib/serverApi";
import { formatDate, titleCase } from "@/lib/formatting";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable } from "@/components/ui/Table";
import { EmptyPanel, Metric, MetricStrip, StatusDot } from "@/components/workbench/Primitives";
import { AccountMappingForm, ExceptionActions, FinancialUpload, PrivateTargetForm } from "@/components/workbench/DataRoomActions";

const tone = (status: string): BadgeTone => status === "ready" || status === "passed" || status === "resolved" || status === "approved" ? "green" : status === "failed" || status === "open" ? "red" : "amber";
const amount = (value: number | string | null) => value === null ? "—" : Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 });

export default async function DataRoomPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId: id } = await params;
  const [overview, sources, mappings, exceptions, reconciliations, facts] = await Promise.all([
    api.getWorkspace(id), api.getSources(id).catch(() => []), api.getAccountMappings(id).catch(() => []),
    api.getImportExceptions(id).catch(() => []), api.getReconciliations(id).catch(() => []), api.getFinancialFacts(id, 100).catch(() => []),
  ]);
  const openExceptions = exceptions.filter((x) => x.state === "open");
  const readySources = sources.filter((x) => x.status === "ready");
  const mappedFacts = facts.filter((x) => x.canonical_account);
  const failedRecon = reconciliations.filter((x) => !["passed", "balanced"].includes(x.status));

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Underwriting data" title="Data room & financial normalization" subtitle="Create a private target, seal each source version, map management accounts, and resolve reconciliation exceptions before numbers flow into the model." />

      {!overview.target && <Card eyebrow="Private target" title="Create the underwriting target" subtitle="A ticker is not required. User-entered information remains explicitly labeled as such."><PrivateTargetForm workspaceId={id} /></Card>}

      <MetricStrip columns={5}>
        <Metric label="Source versions" value={sources.length} detail={`${readySources.length} ready`} />
        <Metric label="Financial facts" value={facts.length === 100 ? "100+" : facts.length} detail="Canonical fact ledger" />
        <Metric label="Mapped facts" value={facts.length ? `${Math.round(mappedFacts.length / facts.length * 100)}%` : "—"} detail="Current result window" tone={facts.length && mappedFacts.length === facts.length ? "positive" : "warning"} />
        <Metric label="Open exceptions" value={openExceptions.length} detail="Requires analyst review" tone={openExceptions.length ? "negative" : "positive"} />
        <Metric label="Reconciliations" value={reconciliations.length} detail={failedRecon.length ? `${failedRecon.length} need attention` : "No unresolved breaks"} tone={failedRecon.length ? "warning" : "positive"} />
      </MetricStrip>

      <Card eyebrow="Ingestion" title="Import management financials" subtitle="The file is hashed and sealed before normalized facts are created."><FinancialUpload workspaceId={id} /></Card>

      <Card eyebrow="Source health" title="Immutable source snapshots" subtitle="Failure and partial states remain visible; they are never represented as clean zeroes.">
        {sources.length ? <DataTable rows={sources} getRowKey={(row) => row.id} columns={[
          { key: "source", header: "Source", render: (row) => <div><div className="font-medium text-ink">{row.source_name}</div><div className="mt-0.5 text-2xs text-faint">{row.filename ?? row.source_type}</div></div> },
          { key: "kind", header: "Kind", render: (row) => titleCase(row.source_kind) },
          { key: "version", header: "Version", align: "right", render: (row) => `v${row.version}` },
          { key: "records", header: "Records", align: "right", render: (row) => row.record_count.toLocaleString() },
          { key: "status", header: "Health", render: (row) => <Badge tone={tone(row.status)}><StatusDot status={row.status} />{row.status}</Badge> },
          { key: "sealed", header: "Sealed", render: (row) => formatDate(row.sealed_at) },
          { key: "hash", header: "Content hash", render: (row) => <span className="font-mono text-2xs" title={row.content_hash}>{row.content_hash.slice(0, 10)}…</span> },
        ]} /> : <EmptyPanel title="No source snapshots" body="Import a CSV or XLSX management pack to create the first immutable source version." />}
      </Card>

      <Card eyebrow="Normalization" title="Account mapping policy" subtitle="Mappings are versioned. Canonical accounts use lowercase snake_case."><div className="space-y-5"><AccountMappingForm workspaceId={id} />{mappings.length ? <DataTable rows={mappings.slice(0, 20)} getRowKey={(row) => row.id} columns={[
        { key: "raw", header: "Management account", render: (row) => row.raw_account },
        { key: "canonical", header: "Canonical account", render: (row) => <span className="font-mono text-xs text-accent">{row.canonical_account}</span> },
        { key: "statement", header: "Statement", render: (row) => titleCase(row.statement) },
        { key: "sign", header: "Sign", align: "right", render: (row) => amount(row.sign_multiplier) },
        { key: "version", header: "Version", align: "right", render: (row) => `v${row.version}` },
        { key: "status", header: "Status", render: (row) => <Badge tone={tone(row.status)}>{row.status}</Badge> },
      ]} /> : <p className="text-xs text-muted">No mapping rules have been approved yet. Unmapped imported facts will remain in the exception queue.</p>}</div></Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card eyebrow="Control queue" title="Import exceptions" subtitle={`${openExceptions.length} open items`}>
          {exceptions.length ? <DataTable rows={exceptions.slice(0, 25)} getRowKey={(row) => row.id} columns={[
            { key: "code", header: "Code", render: (row) => <span className="font-mono text-2xs">{row.code}</span> },
            { key: "message", header: "Exception", render: (row) => <span className="line-clamp-2 max-w-sm">{row.message}</span> },
            { key: "severity", header: "Severity", render: (row) => <Badge tone={row.severity === "high" || row.severity === "critical" ? "red" : "amber"}>{row.severity}</Badge> },
            { key: "action", header: "Review", render: (row) => <ExceptionActions workspaceId={id} item={row} /> },
          ]} /> : <EmptyPanel title="No import exceptions" body="Exceptions will appear when rows cannot be mapped or financial controls fail." />}
        </Card>
        <Card eyebrow="Control totals" title="Balance-sheet reconciliation">
          {reconciliations.length ? <DataTable rows={reconciliations} getRowKey={(row) => row.id} columns={[
            { key: "period", header: "Period", render: (row) => formatDate(row.period_end) },
            { key: "assets", header: "Assets", align: "right", render: (row) => amount(row.assets) },
            { key: "le", header: "Liab. + equity", align: "right", render: (row) => amount(row.liabilities_and_equity) },
            { key: "diff", header: "Difference", align: "right", render: (row) => amount(row.difference) },
            { key: "status", header: "Status", render: (row) => <Badge tone={tone(row.status)}>{row.status}</Badge> },
          ]} /> : <EmptyPanel title="No reconciliation results" body="Balance-sheet controls run during financial import when the required canonical accounts are available." />}
        </Card>
      </div>

      <Card eyebrow="Fact ledger" title="Recent normalized financial facts" subtitle="Every value retains its source snapshot, row locator, period, unit, scale, and row hash.">
        {facts.length ? <DataTable rows={facts.slice(0, 50)} getRowKey={(row) => row.id} columns={[
          { key: "account", header: "Canonical account", render: (row) => row.canonical_account ? <span className="font-mono text-xs text-accent">{row.canonical_account}</span> : <span className="text-warn">Unmapped · {row.raw_account}</span> },
          { key: "statement", header: "Statement", render: (row) => titleCase(row.statement) },
          { key: "period", header: "Period end", render: (row) => formatDate(row.period_end) },
          { key: "type", header: "Period type", render: (row) => titleCase(row.period_type) },
          { key: "value", header: "Value", align: "right", render: (row) => `${row.currency ?? ""} ${amount(row.value)}` },
          { key: "source", header: "Source locator", render: (row) => <span className="font-mono text-2xs">{row.source_locator}</span> },
        ]} /> : <EmptyPanel title="No canonical facts" body="Import management financials to populate the governed fact ledger." />}
      </Card>
    </div>
  );
}
