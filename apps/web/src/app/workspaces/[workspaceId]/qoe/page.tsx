import { api } from "@/lib/serverApi";
import { formatDate, titleCase } from "@/lib/formatting";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable } from "@/components/ui/Table";
import { EmptyPanel, Metric, MetricStrip } from "@/components/workbench/Primitives";
import { QoEAdjustmentForm, QoEDecisionActions } from "@/components/workbench/QoEActions";

const money = (value: number | string | null, currency = "USD") => value === null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(value));
const statusTone = (status: string): BadgeTone => status === "approved" ? "green" : status === "rejected" ? "red" : "amber";

export default async function QoEPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId: id } = await params;
  const [adjustments, bridge] = await Promise.all([api.getQoEAdjustments(id).catch(() => []), api.getQoEBridge(id).catch(() => null)]);
  const proposed = adjustments.filter((x) => x.status === "proposed").length;
  const currency = bridge?.currency ?? adjustments[0]?.currency ?? "USD";

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Quality of earnings" title="Reported-to-underwritten EBITDA bridge" subtitle="Control management, sponsor, and covenant adjustments in a single evidence-backed ledger. Only approved items enter the bridge." />

      <MetricStrip columns={5}>
        <Metric label="Reported EBITDA" value={money(bridge?.reported_ebitda ?? null, currency)} detail={bridge?.period_end ? `LTM ${formatDate(bridge.period_end)}` : "Awaiting mapped EBITDA"} />
        <Metric label="Management EBITDA" value={money(bridge?.management_ebitda ?? null, currency)} detail={`${money(bridge?.management_adjustments ?? 0, currency)} adjustments`} />
        <Metric label="Sponsor EBITDA" value={money(bridge?.sponsor_ebitda ?? null, currency)} detail={`${money(bridge?.sponsor_adjustments ?? 0, currency)} adjustments`} tone={bridge?.sponsor_ebitda !== null && bridge?.sponsor_ebitda !== undefined ? "positive" : "warning"} />
        <Metric label="Covenant EBITDA" value={money(bridge?.covenant_ebitda ?? null, currency)} detail={`${money(bridge?.covenant_adjustments ?? 0, currency)} adjustments`} />
        <Metric label="Review queue" value={proposed} detail={`${adjustments.length - proposed} adjudicated`} tone={proposed ? "warning" : "positive"} />
      </MetricStrip>

      {bridge?.warnings?.length ? <Callout tone="warning" title="Bridge controls">{bridge.warnings.join(" ")}</Callout> : null}
      {!bridge && <Callout tone="muted" title="Bridge not ready">Import and map a reported EBITDA fact before constructing the bridge. Missing data remains incomplete rather than defaulting to zero.</Callout>}

      <Card eyebrow="Adjustment intake" title="Propose an adjustment" subtitle="The amount is signed. Positive values increase EBITDA; negative values reduce it."><QoEAdjustmentForm workspaceId={id} /></Card>

      <Card eyebrow="Review ledger" title="QoE adjustments" subtitle="Approval is explicit, attributable, and immediately reflected in downstream bridge calculations.">
        {adjustments.length ? <DataTable rows={adjustments} getRowKey={(row) => row.id} columns={[
          { key: "item", header: "Adjustment", render: (row) => <div className="max-w-xs"><div className="font-medium text-ink">{row.title}</div><div className="mt-0.5 line-clamp-2 text-2xs text-muted">{row.description || titleCase(row.category)}</div></div> },
          { key: "period", header: "Period", render: (row) => formatDate(row.period_end) },
          { key: "layer", header: "Layer", render: (row) => <Badge tone={row.bridge_layer === "sponsor" ? "indigo" : row.bridge_layer === "covenant" ? "gold" : "slate"}>{row.bridge_layer}</Badge> },
          { key: "flags", header: "Treatment", render: (row) => <div className="flex flex-wrap gap-1">{row.is_recurring && <Badge tone="amber">Recurring</Badge>}{row.is_run_rate && <Badge tone="indigo">Run-rate</Badge>}<Badge tone="neutral">{row.is_cash ? "Cash" : "Non-cash"}</Badge></div> },
          { key: "evidence", header: "Evidence", render: (row) => <div className="max-w-[11rem] text-2xs"><div>{row.evidence_ref ?? "No evidence ref"}</div><div className="truncate text-faint" title={row.source_locator ?? ""}>{row.source_locator ?? "No source locator"}</div></div> },
          { key: "amount", header: "Amount", align: "right", render: (row) => <span className={Number(row.amount) >= 0 ? "text-positive" : "text-negative"}>{money(row.amount, row.currency)}</span> },
          { key: "status", header: "Status", render: (row) => <Badge tone={statusTone(row.status)}>{row.status}</Badge> },
          { key: "decision", header: "Decision", align: "right", render: (row) => <QoEDecisionActions workspaceId={id} adjustment={row} /> },
        ]} /> : <EmptyPanel title="No QoE adjustments" body="Propose the first adjustment once reported EBITDA and supporting QoE evidence are available." />}
      </Card>

      <Card eyebrow="Audit logic" title="Bridge inclusion policy">
        <div className="grid gap-4 text-xs leading-relaxed text-muted md:grid-cols-3">
          <div><strong className="text-ink">Management layer.</strong> Documents management&apos;s own normalization claims without adopting them as the sponsor view.</div>
          <div><strong className="text-ink">Sponsor layer.</strong> Captures the investment team&apos;s underwritten recurring earnings view and flows into leverage and valuation.</div>
          <div><strong className="text-ink">Covenant layer.</strong> Separately models credit-agreement definitions and permitted add-backs.</div>
        </div>
      </Card>
    </div>
  );
}
